
import cv2

camera = cv2.VideoCapture(0, cv2.CAP_V4L2)

if not camera.isOpened():
    raise RuntimeError("Could not open camera")

# Configure MJPEG
camera.set(
    cv2.CAP_PROP_FOURCC,
    cv2.VideoWriter_fourcc(*"MJPG")
)

camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
camera.set(cv2.CAP_PROP_FPS, 30)

# Capture a few frames to allow auto exposure to settle
for _ in range(15):
    ret, frame = camera.read()
    if not ret:
        break

if ret:
    cv2.imwrite("camera_test.jpg", frame)

    print("Image captured successfully!")
    print(f"Resolution: {frame.shape[1]}x{frame.shape[0]}")
    print(f"FPS: {camera.get(cv2.CAP_PROP_FPS)}")
else:
    print("Failed to capture image")

camera.release()

