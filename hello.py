import numpy as np
import pandas as pd


def main():
    print("Hello World")


def numpy_sample():
    print("=== NumPy Sample ===")
    arr = np.array([1, 2, 3, 4, 5])
    print(f"Array: {arr}")
    print(f"Mean: {arr.mean()}")
    print(f"Std:  {arr.std()}")

    matrix = np.arange(9).reshape(3, 3)
    print(f"Matrix:\n{matrix}")
    print(f"Transpose:\n{matrix.T}")


def pandas_sample():
    print("\n=== Pandas Sample ===")
    data = {
        "name": ["Alice", "Bob", "Charlie"],
        "age": [25, 30, 35],
        "score": [88.5, 92.0, 79.3],
    }
    df = pd.DataFrame(data)
    print(df)
    print(f"\nAverage score: {df['score'].mean():.2f}")
    print(f"\nFiltered (age >= 30):\n{df[df['age'] >= 30]}")


if __name__ == "__main__":
    main()
    numpy_sample()
    pandas_sample()
