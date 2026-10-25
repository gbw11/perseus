package com.xenova.backend.auth.api;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

public record SignupRequest(
        @NotBlank(message = "username은 필수입니다.")
        @Pattern(regexp = "^[A-Za-z0-9_-]{4,32}$", message = "username은 4~32자의 영문, 숫자, _, -만 사용할 수 있습니다.")
        String username,
        @NotBlank(message = "nickname은 필수입니다.")
        @Size(max = 50, message = "nickname은 50자 이하여야 합니다.")
        String nickname,
        @NotBlank(message = "password는 필수입니다.")
        @Size(min = 8, max = 64, message = "password는 8~64자여야 합니다.")
        String password) {
}
