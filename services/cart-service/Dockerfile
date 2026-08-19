FROM python:3.12-slim

WORKDIR /app

COPY shared/requirements.txt /app/shared-requirements.txt
COPY services/cart-service/requirements.txt /app/service-requirements.txt
RUN pip install --no-cache-dir -r /app/shared-requirements.txt -r /app/service-requirements.txt

COPY shared/gestalt_shared /app/gestalt_shared
COPY services/cart-service/app /app/app

ENV PYTHONPATH=/app

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
