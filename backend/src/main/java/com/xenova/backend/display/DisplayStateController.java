package com.xenova.backend.display;

import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class DisplayStateController {
    private final DisplayFeedClient feed;

    public DisplayStateController(DisplayFeedClient feed) { this.feed = feed; }

    @GetMapping("/api/v1/display/state")
    public JsonNode state() { return feed.snapshot(); }
}
