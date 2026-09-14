#!/usr/bin/env bash
set -euo pipefail
: "${ECS_CLUSTER:?}" "${MIGRATION_TASK_DEFINITION:?}" "${ECS_API_SERVICE:?}" "${ECS_WORKER_SERVICE:?}" "${ECS_SUBNET_IDS:?}" "${ECS_SECURITY_GROUP:?}" "${ECR_REPOSITORY:?}" "${IMAGE_TAG:?}"
TASK=$(aws ecs run-task --cluster "$ECS_CLUSTER" --task-definition "$MIGRATION_TASK_DEFINITION" --launch-type FARGATE --network-configuration "awsvpcConfiguration={subnets=[$ECS_SUBNET_IDS],securityGroups=[$ECS_SECURITY_GROUP],assignPublicIp=ENABLED}" --query 'tasks[0].taskArn' --output text)
aws ecs wait tasks-stopped --cluster "$ECS_CLUSTER" --tasks "$TASK"
CODE=$(aws ecs describe-tasks --cluster "$ECS_CLUSTER" --tasks "$TASK" --query 'tasks[0].containers[0].exitCode' --output text)
test "$CODE" = "0"
update_service_image() {
  local service="$1"
  local image="${ECR_REPOSITORY}:${IMAGE_TAG}"
  local task_definition
  local new_task_definition
  task_definition=$(aws ecs describe-services --cluster "$ECS_CLUSTER" --services "$service" --query 'services[0].taskDefinition' --output text)
  aws ecs describe-task-definition --task-definition "$task_definition" --query 'taskDefinition' > task-definition.json
  jq --arg image "$image" 'del(.taskDefinitionArn, .revision, .status, .requiresAttributes, .compatibilities, .registeredAt, .registeredBy) | .containerDefinitions |= map(.image = $image)' task-definition.json > task-definition-updated.json
  new_task_definition=$(aws ecs register-task-definition --cli-input-json file://task-definition-updated.json --query 'taskDefinition.taskDefinitionArn' --output text)
  aws ecs update-service --cluster "$ECS_CLUSTER" --service "$service" --task-definition "$new_task_definition" >/dev/null
}
update_service_image "$ECS_API_SERVICE"
update_service_image "$ECS_WORKER_SERVICE"
aws ecs wait services-stable --cluster "$ECS_CLUSTER" --services "$ECS_API_SERVICE" "$ECS_WORKER_SERVICE"
