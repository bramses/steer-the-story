from fastapi import FastAPI, Request, Form, Depends, Response
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from fastapi.openapi.utils import get_openapi
from fastapi.middleware.cors import CORSMiddleware
# from server.openai_logic import process
import dotenv
import os
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from Crypto.Random import get_random_bytes
import base64
import redis
import json
from gpt_error import gpt_error


# load .env file from root directory ../
dotenv.load_dotenv(
    dotenv_path=os.path.join(os.path.dirname(__file__), "../.env")
)


import uuid
from pydantic import BaseModel

class ConditionalModel(BaseModel):
    contains: str
    min: int
    max: int
    user_id: str

origins = [
    "https://chat.openai.com",
    "http://localhost:8001",
]

tags_metadata = [
    {
        "name": "uses-user-id",
        "description": "Uses user ID. This can be retrieved from the unique URL generated.",
    }
]


app = FastAPI(tags_metadata=tags_metadata)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


templates = Jinja2Templates(directory="templates")

user_locations = {}

PROD_URL = os.getenv("PROD_URL")
LOCAL_URL = "http://localhost:8001"

IS_PROD = os.getenv("PROD") == "true"

if IS_PROD:
    BASE_URL = PROD_URL
    # Creating a connection to Redis
    r = redis.Redis(username=os.getenv("REDIS_USERNAME"), host=os.getenv("REDIS_HOST"), password=os.getenv("REDIS_PW"), port=6379, db=0, decode_responses=True, ssl=True)
else:
    BASE_URL = LOCAL_URL
    r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

@app.get("/get_unique_url/")
async def get_unique_url(request: Request):
    unique_url = str(uuid.uuid4())
    return {"unique_url": BASE_URL + "/user/" + unique_url}

@app.get("/user/{user_id}", response_class=HTMLResponse)
async def serve_form(request: Request, user_id: str):
    user_id = user_id.split("/")[-1]
    return templates.TemplateResponse("conditional.html", {"request": request, "user_id": user_id})


key = base64.b64decode(os.getenv("DECODER_API_KEY"))

# https://chat.openai.com/share/0758c0cf-425b-41c2-981a-ebe3eba7633d
def encrypt(plain_text, key):
    if type(plain_text) == float:
        plain_text = str(plain_text)
    cipher = AES.new(key, AES.MODE_CBC)  # Create a new AES cipher object
    ct_bytes = cipher.encrypt(pad(plain_text.encode('utf-8'), AES.block_size))
    iv = cipher.iv  # Initialization vector
    ct = base64.b64encode(iv + ct_bytes).decode('utf-8')  # Encode the IV and ciphertext
    return ct

def decrypt(ct, key):
    ct = base64.b64decode(ct)  # Decode the IV and ciphertext
    iv = ct[:16]  # The IV is the first 16 bytes
    ct_bytes = ct[16:]  # The ciphertext is everything after
    cipher = AES.new(key, AES.MODE_CBC, iv=iv)  # Create a new AES cipher object
    plain_text = unpad(cipher.decrypt(ct_bytes), AES.block_size).decode('utf-8')

    if plain_text.lstrip('-').replace('.','',1).isdigit():
        return float(plain_text)
    return plain_text


# https://chat.openai.com/share/db18ca29-679e-4838-8555-d118aa5b873b
async def redis_keys():
    keys = r.keys('*')
    for key in keys:
        value = r.get(key) # get value associated with key
        if value:
            print(f'{key}: {value}')
    return {"keys": keys}

'''
<!-- create a form with headings for contains and string length post to endpoint /submit-form with form values-->
'''
@app.post("/submit-form")
@gpt_error
async def submit_form(form_results: ConditionalModel):
    user_id = form_results.user_id
    r.set(user_id, json.dumps(form_results.dict()))
    return {"success": True}
    
    

@gpt_error
async def check_conditions(testStr, conditions):
    # Check if length condition exists and is satisfied
    if 'min' in conditions:
        if len(testStr) < conditions['min']:
            return f"Does not meet min length of {conditions['min']} characters. Current length: {len(testStr)}"
        
    if 'max' in conditions:
        if len(testStr) > conditions['max']:
            return f"Exceeds max length of {conditions['max']} characters. Current length: {len(testStr)}"

    # Check if contains condition exists and is satisfied -- compare lowercase
    if 'contains' in conditions:
        if conditions['contains'].lower() not in testStr.lower():
            return f"Does not contain {conditions['contains']}"

    # If all conditions are satisfied
    return "valid"
    # except Exception as e:
    #     prompt = error_wrap.wrap_error(e)
    #     potential_fix = await error_wrap.run_chat_prompt(prompt)
    #     return {"error": str(e), "potential_fix": potential_fix}
    


@app.post("/validate-conditions/{testStr}", tags=["uses-user-id"])
@gpt_error
async def validate_conditions(user_id: str, testStr: str):
    
    # Get the conditions for the user
    user_conditions = json.loads(r.get(user_id))
    # Check if the conditions are satisfied
    return await check_conditions(testStr, user_conditions)
    # except Exception as e:
    #     prompt = error_wrap.wrap_error(e)
    #     potential_fix = await error_wrap.run_chat_prompt(prompt)
    #     print(potential_fix)
    #     return {"error": str(e), "potential_fix": potential_fix}


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title="Steer the Story",
        version="3.0.2",
        description="An API that allows users to put in a conditional and compare it to specified values generated by ChatGPTa",
        routes=app.routes,
    )
    openapi_schema["info"]["x-logo"] = {
        "url": "https://fastapi.tiangolo.com/img/logo-margin/logo-teal.png"
    }
    # if components.schema is not defined add it (for openai error: Error getting system message: {"message":"Could not parse OpenAPI spec for plugin: ['In components section, schemas subsection is not an object']"})
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}
        openapi_schema["components"]["schemas"] = {}
    else:
        if "schemas" not in openapi_schema["components"]:
            openapi_schema["components"]["schemas"] = {}
    app.openapi_schema = openapi_schema
    return app.openapi_schema


@app.get("/.well-known/ai-plugin.json")
async def plugin_manifest():
    file = open("./.well-known/ai-plugin.json", "r")
    return Response(content=file.read(), media_type="application/json")

app.openapi = custom_openapi