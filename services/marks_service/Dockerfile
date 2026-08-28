FROM python:3.12-slim
WORKDIR /app
COPY common /app/common
COPY services/marks_service /app/service
WORKDIR /app/service
RUN pip install --no-cache-dir -r requirements.txt
ENV PYTHONPATH=/app
EXPOSE 8006
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8006"]
