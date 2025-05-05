FROM python:3.11-buster

RUN pip install poetry

COPY . .

RUN poetry install

ENTRYPOINT ["poetry", "run", "fastapi", "run", "vibeify_backend/"]