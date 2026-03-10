WORM_BAITS = ["nightcrawlers", "mealworms", "red worms", "wax worms", "trout worms"]


def recommend_bait(species: str, water_temp_f: float | None, time_of_day: str = "day"):
    species = (species or "").lower()
    if water_temp_f is None:
        return {"confidence": "low", "reason": "missing_live_environment", "baits": ["nightcrawlers", "red worms"]}
    baits = []
    if species in {"trout", "panfish", "crappie"}:
        baits.extend(["mealworms", "wax worms", "trout worms"])
    if species in {"bass", "catfish", "walleye", "trout"}:
        baits.append("nightcrawlers")
    if water_temp_f < 55:
        baits.append("red worms")
    if time_of_day in {"dusk", "night", "dawn"}:
        baits.append("nightcrawlers")
    deduped = list(dict.fromkeys(baits or WORM_BAITS))
    return {"confidence": "medium", "baits": deduped}
