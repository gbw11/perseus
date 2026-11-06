package com.xenova.backend.common.error;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.HttpMediaTypeNotSupportedException;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.servlet.resource.NoResourceFoundException;

@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final PropertyNamingStrategies.NamingBase SNAKE_CASE =
            (PropertyNamingStrategies.NamingBase) PropertyNamingStrategies.SNAKE_CASE;

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(NoResourceFoundException.class)
    ResponseEntity<ErrorResponse> handleMissingRoute(NoResourceFoundException exception) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(ErrorResponse.of("NOT_FOUND", "요청한 경로를 찾을 수 없습니다."));
    }

    @ExceptionHandler(ApiException.class)
    ResponseEntity<ErrorResponse> handleApiException(ApiException exception) {
        return ResponseEntity.status(exception.getStatus())
                .body(ErrorResponse.of(exception.getCode(), exception.getMessage()));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    ResponseEntity<ErrorResponse> handleValidationException(MethodArgumentNotValidException exception) {
        List<ErrorResponse.FieldError> errors = exception.getBindingResult().getFieldErrors().stream()
                .map(error -> new ErrorResponse.FieldError(toJsonName(error.getField()), error.getDefaultMessage()))
                .toList();

        return ResponseEntity.unprocessableEntity()
                .body(new ErrorResponse("VALIDATION_ERROR", "요청 값이 올바르지 않습니다.", errors));
    }

    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    ResponseEntity<ErrorResponse> handleTypeMismatch(MethodArgumentTypeMismatchException exception) {
        List<ErrorResponse.FieldError> errors = List.of(
                new ErrorResponse.FieldError(exception.getName(), "형식이 올바르지 않습니다."));
        return ResponseEntity.unprocessableEntity()
                .body(new ErrorResponse("VALIDATION_ERROR", "요청 값이 올바르지 않습니다.", errors));
    }

    // A missing required query parameter is an input rule violation like a wrong type (contract 8.2: 422).
    @ExceptionHandler(MissingServletRequestParameterException.class)
    ResponseEntity<ErrorResponse> handleMissingParameter(MissingServletRequestParameterException exception) {
        List<ErrorResponse.FieldError> errors = List.of(
                new ErrorResponse.FieldError(exception.getParameterName(), "값이 필요합니다."));
        return ResponseEntity.unprocessableEntity()
                .body(new ErrorResponse("VALIDATION_ERROR", "요청 값이 올바르지 않습니다.", errors));
    }

    // Broken JSON or a value Jackson cannot convert (e.g. an unknown enum) is the client's input error,
    // not a server fault; without this it fell through to the catch-all 500.
    @ExceptionHandler(HttpMessageNotReadableException.class)
    ResponseEntity<ErrorResponse> handleUnreadableBody(HttpMessageNotReadableException exception) {
        return ResponseEntity.unprocessableEntity()
                .body(ErrorResponse.of("VALIDATION_ERROR", "요청 본문을 읽을 수 없습니다."));
    }

    @ExceptionHandler(HttpRequestMethodNotSupportedException.class)
    ResponseEntity<ErrorResponse> handleMethodNotSupported(HttpRequestMethodNotSupportedException exception) {
        return withStatus(exception, "METHOD_NOT_ALLOWED", "지원하지 않는 HTTP 메서드입니다.");
    }

    @ExceptionHandler(HttpMediaTypeNotSupportedException.class)
    ResponseEntity<ErrorResponse> handleMediaTypeNotSupported(HttpMediaTypeNotSupportedException exception) {
        return withStatus(exception, "UNSUPPORTED_MEDIA_TYPE", "지원하지 않는 Content-Type입니다.");
    }

    // Keeps the framework's status and headers (Allow, Accept) with the common error body.
    private static ResponseEntity<ErrorResponse> withStatus(
            org.springframework.web.ErrorResponse exception, String code, String message) {
        return ResponseEntity.status(exception.getStatusCode())
                .headers(exception.getHeaders())
                .body(ErrorResponse.of(code, message));
    }

    // Field names in errors must match the snake_case JSON the client sent.
    private static String toJsonName(String field) {
        return SNAKE_CASE.translate(field);
    }

    @ExceptionHandler(Exception.class)
    ResponseEntity<ErrorResponse> handleUnexpectedException(Exception exception) {
        log.error("Unhandled API exception", exception);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(ErrorResponse.of("INTERNAL_SERVER_ERROR", "서버 오류가 발생했습니다."));
    }
}
