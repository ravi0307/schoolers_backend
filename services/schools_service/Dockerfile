FROM python:3.12-slim
WORKDIR /app
COPY common /app/common
COPY services/schools_service /app/service
WORKDIR /app/service
RUN pip install --no-cache-dir -r requirements.txt
ENV PYTHONPATH=/app
EXPOSE 8002
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
