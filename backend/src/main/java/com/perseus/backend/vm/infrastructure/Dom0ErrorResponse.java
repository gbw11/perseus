package com.perseus.backend.vm.infrastructure;

record Dom0ErrorResponse(String code, String message, String detail, ResourceError resource) {

    record ResourceError(String name, Integer requested, Integer available) {
    }
}
