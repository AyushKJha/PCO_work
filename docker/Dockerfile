FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir \
    'alembic>=1.13' \
    'argon2-cffi>=23.1' \
    'boto3>=1.35' \
    'fastapi>=0.115' \
    'pydantic>=2.7' \
    'sqlalchemy>=2.0' \
    'psycopg[binary]>=3.2' \
    'pyyaml>=6.0' \
    'uvicorn[standard]>=0.30'
COPY apps ./apps
COPY cli ./cli
COPY migrations ./migrations
COPY services ./services
COPY packages ./packages
COPY scripts ./scripts
RUN pip install --no-cache-dir --no-deps -e .

EXPOSE 8000
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
