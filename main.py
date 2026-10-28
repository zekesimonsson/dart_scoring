import cv2
import platform

def main():
    print("=== Dart Scoring System ===")
    print(f"Python: {platform.python_version()}")
    print(f"OpenCV: {cv2.__version__}")

    print("System initialized successfully!")
    print("Waiting for cameras...")

if __name__ == "__main__":
    main()
