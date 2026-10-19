package com.perseus.backend.common.error;

import com.fasterxml.jackson.annotation.JsonInclude;
import java.util.List;

@JsonInclude(JsonInclude.Include.NON_EMPTY)
public record ErrorResponse(String code, String message, List<FieldError> errors) {

    public static ErrorResponse of(String code, String message) {
        return new ErrorResponse(code, message, null);
    }

    public record FieldError(String field, String reason) {
    }
}
