package com.xenova.backend.common.error;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

// Framework input errors must keep the common {code, message} body instead of falling into the catch-all 500.
class GlobalExceptionHandlerTest {

    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        mockMvc = MockMvcBuilders.standaloneSetup(new SampleController())
                .setControllerAdvice(new GlobalExceptionHandler())
                .build();
    }

    @Test
    void malformedJsonBodyIsValidationError() throws Exception {
        mockMvc.perform(post("/sample").contentType(MediaType.APPLICATION_JSON).content("{\"name\":"))
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"));
    }

    @Test
    void missingRequiredParameterIsValidationErrorWithField() throws Exception {
        mockMvc.perform(post("/sample/required"))
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.errors[0].field").value("vm_id"));
    }

    @Test
    void unsupportedMethodKeepsStatusAndAllowHeader() throws Exception {
        mockMvc.perform(put("/sample").contentType(MediaType.APPLICATION_JSON).content("{}"))
                .andExpect(status().isMethodNotAllowed())
                .andExpect(header().exists("Allow"))
                .andExpect(jsonPath("$.code").value("METHOD_NOT_ALLOWED"));
    }

    @Test
    void unsupportedContentTypeIs415() throws Exception {
        mockMvc.perform(post("/sample").contentType(MediaType.TEXT_PLAIN).content("x"))
                .andExpect(status().isUnsupportedMediaType())
                .andExpect(jsonPath("$.code").value("UNSUPPORTED_MEDIA_TYPE"));
    }

    @Test
    void apiExceptionKeepsItsOwnStatusAndCode() throws Exception {
        mockMvc.perform(get("/sample/conflict"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("VM_NAME_CONFLICT"));
    }

    @RestController
    static class SampleController {

        @PostMapping("/sample")
        Map<String, Object> create(@RequestBody Map<String, Object> body) {
            return body;
        }

        @PostMapping("/sample/required")
        String required(@RequestParam("vm_id") String vmId) {
            return vmId;
        }

        @GetMapping("/sample/conflict")
        String conflict() {
            throw new ApiException(HttpStatus.CONFLICT, "VM_NAME_CONFLICT", "conflict");
        }
    }
}
