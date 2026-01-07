from flask import Flask, request, jsonify, send_from_directory
from pymongo import MongoClient
from bson.objectid import ObjectId
from werkzeug.utils import secure_filename
import os
import base64
import requests

app = Flask(__name__)

client = MongoClient(os.environ.get("MONGO_URI"))
db = client["school"]
students = db["students"]

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = "fasthoster/test"
GITHUB_BRANCH = "main"
GITHUB_FOLDER = "up"

CACHE_FOLDER = "/tmp/uploads"
os.makedirs(CACHE_FOLDER, exist_ok=True)

# hellp
def upload_to_github(file, filename):
    content = file.read()
    content_b64 = base64.b64encode(content).decode("utf-8")
    path = f"{GITHUB_FOLDER}/{filename}"

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"

    data = {
        "message": f"Upload {filename}",
        "content": content_b64,
        "branch": GITHUB_BRANCH
    }

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }

    response = requests.put(url, json=data, headers=headers)

    if response.status_code in [200, 201]:
        return True
    else:
        print("GitHub upload failed:", response.json())
        return False


def cache_images_from_github():
    for student in students.find({"image_url": {"$exists": True}}):
        github_url = student.get("image_url")
        if github_url:
            ext = os.path.splitext(github_url)[-1]
            filename = f"{student['_id']}{ext}"
            local_path = os.path.join(CACHE_FOLDER, filename)

            if not os.path.exists(local_path):
                try:
                    r = requests.get(github_url)
                    if r.status_code == 200:
                        with open(local_path, "wb") as f:
                            f.write(r.content)
                        # Update student with cached URL
                        students.update_one(
                            {"_id": student["_id"]},
                            {"$set": {"local_image_url": f"/cache/{filename}"}}
                        )
                        print(f"Cached image: {filename}")
                except Exception as e:
                    print(f"Failed to cache {github_url}: {e}")


def full_url(path: str) -> str:
    host_url = request.url_root.rstrip("/")
    return f"{host_url}{path}"


def add_local_url(student: dict) -> dict:
    if "local_image_url" in student:
        student["image_url"] = full_url(student["local_image_url"])
    return student


@app.route("/cache/<filename>")
def serve_cached_image(filename):
    return send_from_directory(CACHE_FOLDER, filename)


@app.route("/entries", methods=["POST"])
def add_student():
    name = request.form.get("name")
    age = request.form.get("age")
    image = request.files.get("image")

    if not name or not age or not image:
        return jsonify({"error": "please provide complete data"}), 400

    student = {
        "name": name,
        "age": int(age)
    }

    result = students.insert_one(student)
    student_id = str(result.inserted_id)

    ext = os.path.splitext(secure_filename(image.filename))[1]
    filename = f"{student_id}{ext}"

    local_path = os.path.join(CACHE_FOLDER, filename)
    image.seek(0)
    image.save(local_path)
    
    image.seek(0)
    upload_to_github(image, filename)

    local_url = f"/cache/{filename}"
    students.update_one({"_id": ObjectId(student_id)}, {"$set": {"local_image_url": local_url}})

    student["_id"] = student_id
    student["local_image_url"] = local_url
    add_local_url(student)  # converts to full URL

    return jsonify(student), 201


@app.route("/entries", methods=["GET"])
def get_students():
    data = []
    for student in students.find():
        student["_id"] = str(student["_id"])
        add_local_url(student)
        data.append(student)
    return jsonify(data)


@app.route("/entries/<id>", methods=["GET"])
def get_student(id):
    student = students.find_one({"_id": ObjectId(id)})
    if not student:
        return jsonify({"error": "student not found"}), 404
    student["_id"] = str(student["_id"])
    add_local_url(student)
    return jsonify(student)


@app.route("/entries/<id>", methods=["PUT"])
def update_student(id):
    update_data = {}
    name = request.form.get("name")
    age = request.form.get("age")
    image = request.files.get("image")

    if name:
        update_data["name"] = name
    if age:
        update_data["age"] = int(age)
    if image:
        ext = os.path.splitext(secure_filename(image.filename))[1]
        filename = f"{id}{ext}"
        local_path = os.path.join(CACHE_FOLDER, filename)
        image.seek(0)
        image.save(local_path)
        update_data["local_image_url"] = f"/cache/{filename}"
        image.seek(0)
        upload_to_github(image, filename)

    if not update_data:
        return jsonify({"error": "no new data"}), 400

    result = students.update_one({"_id": ObjectId(id)}, {"$set": update_data})
    if result.matched_count == 0:
        return jsonify({"error": "student not found"}), 404

    student = students.find_one({"_id": ObjectId(id)})
    student["_id"] = str(student["_id"])
    add_local_url(student)
    return jsonify(student)


@app.route("/entries/<id>", methods=["DELETE"])
def remove_student(id):
    student = students.find_one({"_id": ObjectId(id)})
    if not student:
        return jsonify({"error": "student not found"}), 404

    if "local_image_url" in student:
        local_file = student["local_image_url"].replace("/cache/", "")
        local_path = os.path.join(CACHE_FOLDER, local_file)
        if os.path.exists(local_path):
            os.remove(local_path)

    students.delete_one({"_id": ObjectId(id)})
    return jsonify({"message": "student removed"})


print("Caching GitHub images...")
cache_images_from_github()
print("Cache ready!")

if __name__ == "__main__":
    app.run(debug=True)
