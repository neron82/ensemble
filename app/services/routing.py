def select_speakers(room, characters, target_character_id=None, history=()):
    """Resolve an explicit target or the next ordered room member, never mentions."""
    by_id = {character["id"]: character for character in characters}
    members = room["character_ids"]
    if target_character_id is not None and target_character_id not in members:
        raise ValueError("The selected character is not a member of this room")
    if not members:
        raise ValueError("Add a character to this room before chatting")
    if any(member not in by_id for member in members):
        raise ValueError("A room character no longer exists")
    if room["kind"] == "direct" and len(members) != 1:
        raise ValueError("Direct rooms require exactly one character")
    if target_character_id is not None:
        return [by_id[target_character_id]]
    if room["kind"] == "direct":
        return [by_id[members[0]]]
    start = 0
    for message in reversed(history):
        if message["role"] == "assistant" and message["character_id"] in members:
            start = (members.index(message["character_id"]) + 1) % len(members)
            break
    ordered = members[start:] + members[:start]
    return [by_id[member] for member in ordered]
