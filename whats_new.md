**YOLO mode actually auto-approves again.**

YOLO mode had quietly stopped working — you'd switch it on, but Claude would still stop and ask permission on the very next thing it did. The cause: YOLO was being remembered against an internal ID that Claude throws away and regenerates after *every single turn*, so the setting was effectively forgotten the moment a turn ended (and it saved nothing at all if you flipped it on while the chat was sitting idle).

Now YOLO is pinned to the chat itself. Turn it on once and it stays on for that whole chat — every file edit, terminal command and tool runs without asking — while every other chat keeps asking like normal. Pick it from the mode menu next to the model name (the one-time "are you sure?" still applies).
