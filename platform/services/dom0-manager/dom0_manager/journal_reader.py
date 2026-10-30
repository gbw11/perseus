"""Bounded binary line framing; drain all lines from each readable pipe chunk."""
class JournalLines:
    def __init__(self, limit=65536):
        self.limit = limit
        self.buffer = b""
        self.discarding = False

    def feed(self, chunk):
        self.buffer += chunk
        lines = []
        while b"\n" in self.buffer:
            line, _, self.buffer = self.buffer.partition(b"\n")
            if not self.discarding and len(line) <= self.limit:
                lines.append(line)
            self.discarding = False
        if len(self.buffer) > self.limit:
            self.buffer = b""
            self.discarding = True
        return lines
