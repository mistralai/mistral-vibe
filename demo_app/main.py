#!/usr/bin/env python3
"""
A simple demo app that demonstrates basic functionality.
This app greets users and can perform simple calculations.
"""


def greet(name: str) -> str:
    """Greet the user by name."""
    return f"Hello, {name}! Welcome to the demo app."


def add_numbers(a: float, b: float) -> float:
    """Add two numbers and return the result."""
    return a + b


def multiply_numbers(a: float, b: float) -> float:
    """Multiply two numbers and return the result."""
    return a * b


def main():
    """Main entry point for the demo app."""
    print("=== Demo App ===")
    print("1. Greet me")
    print("2. Add numbers")
    print("3. Multiply numbers")
    print("4. Exit")
    
    while True:
        choice = input("\nEnter your choice (1-4): ")
        
        if choice == "1":
            name = input("Enter your name: ")
            print(greet(name))
        elif choice == "2":
            try:
                a = float(input("Enter first number: "))
                b = float(input("Enter second number: "))
                result = add_numbers(a, b)
                print(f"Result: {a} + {b} = {result}")
            except ValueError:
                print("Invalid input. Please enter numbers only.")
        elif choice == "3":
            try:
                a = float(input("Enter first number: "))
                b = float(input("Enter second number: "))
                result = multiply_numbers(a, b)
                print(f"Result: {a} * {b} = {result}")
            except ValueError:
                print("Invalid input. Please enter numbers only.")
        elif choice == "4":
            print("Goodbye!")
            break
        else:
            print("Invalid choice. Please try again.")


if __name__ == "__main__":
    main()