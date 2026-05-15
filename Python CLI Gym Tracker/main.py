import json
import time
from pathlib import Path
from datetime import datetime

DATA_FILE = Path("data.json")


def load_data():
    if not DATA_FILE.exists():
        return {"routines": [], "workouts": []}

    with open(DATA_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


def pause():
    input("\nPress Enter to continue...")


def create_routine(data):
    name = input("Routine name: ").strip()
    exercises = []

    while True:
        exercise = input("Add exercise name, or leave empty to finish: ").strip()

        if not exercise:
            break

        sets = int(input("Target sets: "))
        reps = int(input("Target reps: "))

        exercises.append({
            "name": exercise,
            "sets": sets,
            "reps": reps
        })

    if name and exercises:
        data["routines"].append({
            "name": name,
            "exercises": exercises
        })
        save_data(data)
        print("Routine saved.")
    else:
        print("Routine not saved.")


def show_routines(data):
    if not data["routines"]:
        print("No routines yet.")
        return

    for i, routine in enumerate(data["routines"], start=1):
        print(f"\n{i}. {routine['name']}")
        for exercise in routine["exercises"]:
            print(f"   - {exercise['name']}: {exercise['sets']} sets x {exercise['reps']} reps")


def rest_timer(seconds=60):
    print(f"Rest timer started: {seconds} seconds")

    for remaining in range(seconds, 0, -1):
        print(f"\rRest: {remaining}s", end="")
        time.sleep(1)

    print("\nRest finished!")


def log_workout(data):
    workout = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "sets": []
    }

    show_routines(data)

    use_routine = input("\nUse routine number, or press Enter for empty workout: ").strip()

    selected_exercises = []

    if use_routine:
        try:
            routine = data["routines"][int(use_routine) - 1]
            selected_exercises = [exercise["name"] for exercise in routine["exercises"]]
        except (ValueError, IndexError):
            print("Invalid routine. Starting empty workout.")

    while True:
        if selected_exercises:
            print("\nRoutine exercises:")
            for i, exercise in enumerate(selected_exercises, start=1):
                print(f"{i}. {exercise}")

            choice = input("Choose exercise number, type custom exercise, or leave empty to finish: ").strip()

            if not choice:
                break

            if choice.isdigit() and 1 <= int(choice) <= len(selected_exercises):
                exercise = selected_exercises[int(choice) - 1]
            else:
                exercise = choice
        else:
            exercise = input("\nExercise name, or leave empty to finish: ").strip()
            if not exercise:
                break

        weight = float(input("Weight: "))
        reps = int(input("Reps: "))

        workout["sets"].append({
            "exercise": exercise,
            "weight": weight,
            "reps": reps
        })

        print("Set logged.")

        start_timer = input("Start 60 second rest timer? y/n: ").lower().strip()
        if start_timer == "y":
            rest_timer(60)

    if workout["sets"]:
        data["workouts"].append(workout)
        save_data(data)
        print("Workout saved.")
    else:
        print("No sets logged.")


def show_history(data):
    if not data["workouts"]:
        print("No workout history yet.")
        return

    for workout in data["workouts"]:
        print(f"\nWorkout: {workout['date']}")
        total_volume = 0

        for set_entry in workout["sets"]:
            volume = set_entry["weight"] * set_entry["reps"]
            total_volume += volume

            print(
                f"- {set_entry['exercise']}: "
                f"{set_entry['weight']} kg x {set_entry['reps']} reps"
            )

        print(f"Total volume: {total_volume:.1f}")


def show_progress(data):
    exercise_name = input("Exercise to check progress: ").strip().lower()

    best_weight = 0
    best_volume = 0

    for workout in data["workouts"]:
        for set_entry in workout["sets"]:
            if set_entry["exercise"].lower() == exercise_name:
                weight = set_entry["weight"]
                volume = set_entry["weight"] * set_entry["reps"]

                best_weight = max(best_weight, weight)
                best_volume = max(best_volume, volume)

    if best_weight == 0:
        print("No data for that exercise.")
    else:
        print(f"Best weight: {best_weight} kg")
        print(f"Best set volume: {best_volume:.1f}")


def main():
    data = load_data()

    while True:
        print("\n=== Python Gym Tracker ===")
        print("1. Create routine")
        print("2. Show routines")
        print("3. Log workout")
        print("4. Show workout history")
        print("5. Show progress / PR")
        print("6. Exit")

        choice = input("Choose option: ").strip()

        if choice == "1":
            create_routine(data)
            pause()
        elif choice == "2":
            show_routines(data)
            pause()
        elif choice == "3":
            log_workout(data)
            pause()
        elif choice == "4":
            show_history(data)
            pause()
        elif choice == "5":
            show_progress(data)
            pause()
        elif choice == "6":
            print("Goodbye!")
            break
        else:
            print("Invalid choice.")


if __name__ == "__main__":
    main()