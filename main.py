from dart_scoring.scoring import calculate_score


def main():
    print("=== Dart Scoring System ===")

    test_points = [
        (0.0, 0.0),     # Bullseye
        (0.0, -0.5),    # Single 20
        (0.0, -0.6),    # Triple 20
        (0.0, -0.96),   # Double 20
        (0.0, -1.1),    # Miss
    ]

    for x, y in test_points:
        score = calculate_score(x, y)
        print(f"({x:.2f}, {y:.2f}) -> {score} points")


if __name__ == "__main__":
    main()
