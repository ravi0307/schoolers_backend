# Schoolers Auth Service - Complete Testing Guide

## ⚠️ Current Status
The microservices are running but require a PostgreSQL database to function. To fully test the auth service, you need to:

1. **Install PostgreSQL** (if not already installed)
2. **Create the database and initialize schema**
3. **Add test users**

---

## PostgreSQL Setup

### Installation (macOS)
```bash
brew install postgresql
brew services start postgresql
```

### Create Database & Initialize
```bash
# Create the database
createdb schoolers

# Initialize schema (need to first obtain the schema file or use SQLAlchemy)
# If you have the schema file:
psql schoolers < path/to/schoolers_schema_and_data.sql

# Or create tables via SQLAlchemy:
python3 -c "
from common.database import Base, engine
from common.models import *
Base.metadata.create_all(bind=engine)
print('✓ Tables created')
"

# Add test user
python3 -c "
from common.database import SessionLocal
from common.models import User
from common.security import hash_password

db = SessionLocal()
user = User(
    username='admin',
    password_hash=hash_password('admin123'),
    role='admin',
    school_id=1
)
db.add(user)
db.commit()
print(f'✓ Admin user created (ID: {user.user_id})')
db.close()
"
```

---

## Auth Service Endpoints

### Base URL (via Gateway)
```
http://127.0.0.1:8000/api/v1/auth
```

### Direct Service URL
```
http://127.0.0.1:8001/api/v1/auth
```

---

## 🔑 Endpoint: LOGIN

**POST** `/auth/login`

Authenticates a user and returns JWT tokens.

### Request
```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin",
    "password": "admin123"
  }'
```

### Response (200 OK)
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIiwicm9sZSI6ImFkbWluIiwic2Nob29sX2lkIjoxLCJ0eXBlIjoiYWNjZXNzIiwiZXhwIjoxNjkyNzI1MjAwfQ.xyz",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIiwidHlwZSI6InJlZnJlc2giLCJleHAiOjE2OTUzMTcyMDB9.xyz",
  "token_type": "bearer",
  "role": "admin",
  "school_id": 1,
  "user_id": 1,
  "linked_person_id": null
}
```

### Error Response (401)
```json
{
  "detail": "Invalid username or password"
}
```

### Request Schema
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| username | string | ✓ | User's login username |
| password | string | ✓ | User's login password |

### Response Schema
| Field | Type | Description |
|-------|------|-------------|
| access_token | string | JWT token for API requests (expires in 8 hours) |
| refresh_token | string | JWT token to get new access token (expires in 14 days) |
| token_type | string | Always "bearer" |
| role | string | User role (admin, teacher, student, parent, school_admin) |
| school_id | integer | Associated school ID |
| user_id | integer | User's unique ID |
| linked_person_id | integer | ID of linked person record (teacher/student/parent) |

---

## 🔄 Endpoint: REFRESH TOKEN

**POST** `/auth/refresh`

Get a new access token using a refresh token (useful when access token expires).

### Request
```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{
    "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
  }'
```

### Response (200 OK)
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIiwicm9sZSI6ImFkbWluIiwic2Nob29sX2lkIjoxLCJ0eXBlIjoiYWNjZXNzIiwiZXhwIjoxNjkyNzI1MjAwfQ.xyz",
  "token_type": "bearer"
}
```

### Error Response (401)
```json
{
  "detail": "Invalid or expired refresh token"
}
```

---

## 👤 Endpoint: GET CURRENT USER

**GET** `/auth/me`

Retrieve the current authenticated user's information.

### Request
```bash
curl -X GET http://127.0.0.1:8000/api/v1/auth/me \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

### Response (200 OK)
```json
{
  "user_id": 1,
  "role": "admin",
  "school_id": 1,
  "linked_person_id": null
}
```

### Error Response (401)
```json
{
  "detail": "Not authenticated"
}
```

---

## Complete Test Flow (Bash Script)

Save this as `test_auth.sh` and run with `bash test_auth.sh`

```bash
#!/bin/bash

BASE_URL="http://127.0.0.1:8000/api/v1/auth"

echo "========================================="
echo "  Schoolers Auth Service Testing"
echo "========================================="
echo ""

# Step 1: Login
echo "1️⃣  Logging in..."
LOGIN_RESPONSE=$(curl -s -X POST "$BASE_URL/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin",
    "password": "admin123"
  }')

echo "Response:"
echo "$LOGIN_RESPONSE" | python3 -m json.tool

# Extract tokens
ACCESS_TOKEN=$(echo "$LOGIN_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null)
REFRESH_TOKEN=$(echo "$LOGIN_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin)['refresh_token'])" 2>/dev/null)

if [ -z "$ACCESS_TOKEN" ]; then
  echo "❌ Login failed!"
  exit 1
fi

echo ""
echo "✓ Login successful"
echo "  Access Token: ${ACCESS_TOKEN:0:50}..."
echo "  Refresh Token: ${REFRESH_TOKEN:0:50}..."
echo ""

# Step 2: Get current user
echo "2️⃣  Fetching current user..."
curl -s -X GET "$BASE_URL/me" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | python3 -m json.tool

echo ""

# Step 3: Refresh token
echo "3️⃣  Refreshing access token..."
curl -s -X POST "$BASE_URL/refresh" \
  -H "Content-Type: application/json" \
  -d "{\"refresh_token\": \"$REFRESH_TOKEN\"}" | python3 -m json.tool

echo ""
echo "========================================="
echo "✓ All tests completed successfully!"
echo "========================================="
```

---

## Test Users (After Setup)

### Default Test Credentials
```
Username: admin
Password: admin123
Role: admin
```

To add more test users:

```python
from common.database import SessionLocal
from common.models import User
from common.security import hash_password

db = SessionLocal()

# Add teacher
teacher = User(
    username='teacher1',
    password_hash=hash_password('teacher123'),
    role='teacher',
    school_id=1,
    linked_person_id=1  # Foreign key to teachers table
)
db.add(teacher)

# Add student
student = User(
    username='student1',
    password_hash=hash_password('student123'),
    role='student',
    school_id=1,
    linked_person_id=1  # Foreign key to students table
)
db.add(student)

db.commit()
db.close()
```

---

## Token Structure

### Access Token (JWT)
Payload contains:
- `sub`: User ID
- `role`: User role
- `school_id`: Associated school ID
- `linked_person_id`: ID of linked person record
- `type`: "access"
- `exp`: Expiration timestamp

### Refresh Token (JWT)
Payload contains:
- `sub`: User ID
- `type`: "refresh"
- `exp`: Expiration timestamp

---

## Error Codes

| Status | Error | Meaning |
|--------|-------|---------|
| 200 | - | Success |
| 401 | Invalid username or password | Login failed |
| 401 | Invalid or expired refresh token | Refresh token invalid/expired |
| 401 | Not authenticated | Missing or invalid access token |
| 500 | Database connection error | PostgreSQL not running |

---

## Usage with Other Services

Once authenticated, use the access token in the Authorization header for all subsequent API calls:

```bash
# Example: Get schools list
curl -X GET http://127.0.0.1:8000/api/v1/schools \
  -H "Authorization: Bearer $ACCESS_TOKEN"

# Example: Get attendance records
curl -X GET http://127.0.0.1:8000/api/v1/attendance \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

---

## Troubleshooting

### PostgreSQL Not Running
```bash
# Start PostgreSQL (macOS)
brew services start postgresql

# Check status
brew services list | grep postgresql
```

### Database Not Found
```bash
# Create the database
createdb schoolers

# Verify it exists
psql -l | grep schoolers
```

### Connection Refused
- Ensure PostgreSQL is running on port 5432
- Check `.env` file DATABASE_URL is correct
- Verify network connectivity: `psql -h 127.0.0.1 -U postgres`

