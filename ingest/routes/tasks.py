"""Task queue endpoints."""
from fastapi import APIRouter
from services.queue import enqueue, get_task, list_tasks

router = APIRouter()


@router.get("")
def get_tasks(limit: int = 20):
    """List recent tasks."""
    return {"tasks": list_tasks(limit)}


@router.get("/{task_id}")
def get_task_status(task_id: int):
    """Get task by ID."""
    task = get_task(task_id)
    if not task:
        return {"error": "Task not found"}
    return task


@router.post("/enqueue")
def enqueue_task(data: dict):
    """Manually enqueue a task."""
    task_type = data.get("type", "")
    payload = data.get("payload")
    if not task_type:
        return {"error": "Missing task type"}
    task_id = enqueue(task_type, payload)
    return {"task_id": task_id, "status": "queued"}
