import json
import urllib.request
import urllib.error

data = {
    "message": "Change my GPA in education to 4.0",
    "context": {
        "title": "Warehouse Associate",
        "education": [
            {
                "school": "Overland High School",
                "degree": "High School Diploma",
                "year": "2026",
                "gpa": "3.4",
                "coursework": "Math"
            }
        ]
    },
    "provider": "auto",
    "history": []
}

req = urllib.request.Request(
    'http://127.0.0.1:5001/resume/ai-chat',
    data=json.dumps(data).encode('utf-8'),
    headers={'Content-Type': 'application/json'}
)

try:
    response = urllib.request.urlopen(req)
    res_data = json.loads(response.read().decode('utf-8'))
    print("AI Reply:", res_data.get("reply"))
    print("Resume Data:", json.dumps(res_data.get("resume_data"), indent=2))
except urllib.error.URLError as e:
    print("Error:", e)
