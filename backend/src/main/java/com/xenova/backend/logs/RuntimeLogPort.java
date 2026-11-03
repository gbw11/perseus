package com.xenova.backend.logs;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.Map;

public interface RuntimeLogPort {

    JsonNode read(Map<String, Object> filters, boolean sources);
}
