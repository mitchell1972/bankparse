from fastapi import FastAPI
from fastapi.responses import JSONResponse
import sys, os

app = FastAPI()

@app.get("/api/debug")
async def debug():
    errors = []
    
    try:
        import bcrypt
        errors.append(f"bcrypt: OK ({bcrypt.__version__})")
    except Exception as e:
        errors.append(f"bcrypt: FAIL ({e})")
    
    try:
        import itsdangerous
        errors.append(f"itsdangerous: OK")
    except Exception as e:
        errors.append(f"itsdangerous: FAIL ({e})")
    
    try:
        import libsql_experimental
        errors.append(f"libsql_experimental: OK")
    except Exception as e:
        errors.append(f"libsql_experimental: FAIL ({e})")
    
    try:
        import stripe
        errors.append(f"stripe: OK")
    except Exception as e:
        errors.append(f"stripe: FAIL ({e})")
    
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from database import init_db
        errors.append("database: OK")
    except Exception as e:
        errors.append(f"database: FAIL ({e})")
    
    return JSONResponse({"imports": errors, "python": sys.version})
