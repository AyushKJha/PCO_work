"""Queue transport seams for the durable worker.

The production implementation is a thin boto3 SQS adapter.  ``InMemoryQueue``
is intentionally feature-compatible for offline tests and local development.
Neither adapter executes work; the runtime owns acknowledgement and retry
policy so a message cannot be acknowledged before its database state is safe.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import json
from typing import Any, Protocol
from uuid import uuid4

MAX_SQS_VISIBILITY_TIMEOUT = 43_200


def bounded_visibility_timeout(value: int) -> int:
    """Clamp visibility to the SQS contract (0 through 12 hours)."""
    return max(0, min(int(value), MAX_SQS_VISIBILITY_TIMEOUT))


@dataclass(frozen=True)
class QueueMessage:
    message_id: str
    receipt_handle: str
    body: dict[str, Any]
    receive_count: int = 1


class QueuePublisher(Protocol):
    def publish(self, job_id: str, payload: dict[str, Any] | None = None, *, delay_seconds: int = 0) -> str: ...


class QueueConsumer(Protocol):
    def receive(self, *, max_messages: int = 1, visibility_timeout: int = 60, wait_time_seconds: int = 0) -> list[QueueMessage]: ...
    def acknowledge(self, message: QueueMessage) -> None: ...
    def retry(self, message: QueueMessage, *, visibility_timeout: int) -> None: ...
    def extend_visibility(self, message: QueueMessage, visibility_timeout: int) -> None: ...
    def move_to_dlq(self, message: QueueMessage) -> None: ...


class SQSQueuePublisher:
    def __init__(self, queue_url: str, *, client: Any = None) -> None:
        if not queue_url:
            raise ValueError("queue_url is required")
        if client is None:
            import boto3
            client = boto3.client("sqs")
        self.queue_url, self.client = queue_url, client

    def publish(self, job_id: str, payload: dict[str, Any] | None = None, *, delay_seconds: int = 0) -> str:
        if not job_id:
            raise ValueError("job_id is required")
        body = {**(payload or {}), "job_id": job_id}
        response = self.client.send_message(
            QueueUrl=self.queue_url,
            MessageBody=json.dumps(body, separators=(",", ":"), sort_keys=True),
            DelaySeconds=max(0, min(int(delay_seconds), 900)),
        )
        return str(response.get("MessageId", ""))


class SQSQueueConsumer:
    def __init__(self, queue_url: str, *, dlq_url: str | None = None, client: Any = None) -> None:
        if not queue_url:
            raise ValueError("queue_url is required")
        if client is None:
            import boto3
            client = boto3.client("sqs")
        self.queue_url, self.dlq_url, self.client = queue_url, dlq_url, client

    def receive(self, *, max_messages: int = 1, visibility_timeout: int = 60, wait_time_seconds: int = 0) -> list[QueueMessage]:
        response = self.client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=max(1, min(int(max_messages), 10)),
            VisibilityTimeout=bounded_visibility_timeout(visibility_timeout),
            WaitTimeSeconds=max(0, min(int(wait_time_seconds), 20)),
            AttributeNames=["ApproximateReceiveCount"],
        )
        messages: list[QueueMessage] = []
        for raw in response.get("Messages", []):
            try:
                body = json.loads(raw.get("Body", "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                body = {"_invalid_body": raw.get("Body")}
            attrs = raw.get("Attributes", {}) or {}
            try:
                receive_count = max(1, int(attrs.get("ApproximateReceiveCount", 1)))
            except (TypeError, ValueError):
                receive_count = 1
            messages.append(QueueMessage(str(raw.get("MessageId", "")), str(raw.get("ReceiptHandle", "")), body, receive_count))
        return messages

    def acknowledge(self, message: QueueMessage) -> None:
        self.client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=message.receipt_handle)

    def retry(self, message: QueueMessage, *, visibility_timeout: int) -> None:
        self.client.change_message_visibility(
            QueueUrl=self.queue_url,
            ReceiptHandle=message.receipt_handle,
            VisibilityTimeout=bounded_visibility_timeout(visibility_timeout),
        )

    def extend_visibility(self, message: QueueMessage, visibility_timeout: int) -> None:
        self.client.change_message_visibility(QueueUrl=self.queue_url, ReceiptHandle=message.receipt_handle, VisibilityTimeout=bounded_visibility_timeout(visibility_timeout))

    def move_to_dlq(self, message: QueueMessage) -> None:
        if not self.dlq_url:
            raise RuntimeError("DLQ is not configured; refusing to acknowledge message")
        self.client.send_message(
            QueueUrl=self.dlq_url,
            MessageBody=json.dumps(message.body, separators=(",", ":"), sort_keys=True),
        )
        self.acknowledge(message)


# Verb aliases keep the transport seam ergonomic for callers that use the
# underlying SQS vocabulary while the runtime uses acknowledgement semantics.
SQSQueuePublisher.send = SQSQueuePublisher.publish  # type: ignore[attr-defined]
SQSQueueConsumer.poll = SQSQueueConsumer.receive  # type: ignore[attr-defined]
SQSQueueConsumer.delete = SQSQueueConsumer.acknowledge  # type: ignore[attr-defined]
SQSQueueConsumer.change_visibility = SQSQueueConsumer.retry  # type: ignore[attr-defined]
SQSQueueConsumer.send_to_dlq = SQSQueueConsumer.move_to_dlq  # type: ignore[attr-defined]


@dataclass
class _InMemoryEnvelope:
    message: QueueMessage
    delay: int = 0


class InMemoryQueue:
    """Deterministic queue adapter with explicit retry and DLQ behavior."""
    def __init__(self) -> None:
        self._messages: deque[_InMemoryEnvelope] = deque()
        self.dead_letters: list[QueueMessage] = []

    def publish(self, job_id: str, payload: dict[str, Any] | None = None, *, delay_seconds: int = 0) -> str:
        message_id = str(uuid4())
        self._messages.append(_InMemoryEnvelope(QueueMessage(message_id, message_id, {**(payload or {}), "job_id": job_id})))
        return message_id

    def receive(self, *, max_messages: int = 1, visibility_timeout: int = 60, wait_time_seconds: int = 0) -> list[QueueMessage]:
        del visibility_timeout, wait_time_seconds
        result: list[QueueMessage] = []
        while self._messages and len(result) < max(1, min(int(max_messages), 10)):
            envelope = self._messages.popleft()
            if envelope.delay:
                envelope.delay -= 1
                self._messages.append(envelope)
                continue
            result.append(envelope.message)
        return result

    def acknowledge(self, message: QueueMessage) -> None:
        del message

    def retry(self, message: QueueMessage, *, visibility_timeout: int) -> None:
        del visibility_timeout
        retried = QueueMessage(message.message_id, message.receipt_handle, message.body, message.receive_count + 1)
        self._messages.append(retried and _InMemoryEnvelope(retried))

    def extend_visibility(self, message: QueueMessage, visibility_timeout: int) -> None:
        del message, visibility_timeout

    def move_to_dlq(self, message: QueueMessage) -> None:
        self.dead_letters.append(message)
