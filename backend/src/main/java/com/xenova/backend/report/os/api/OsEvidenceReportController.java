package com.xenova.backend.report.os.api;

import com.xenova.backend.report.os.application.OsEvidenceReport;
import com.xenova.backend.report.os.application.OsEvidenceService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/reports/os-evidence")
public class OsEvidenceReportController {

    private final OsEvidenceService service;

    public OsEvidenceReportController(OsEvidenceService service) {
        this.service = service;
    }

    // A failed verification is still a successful report: 200 with verdict FAIL or BLOCKED.
    @GetMapping
    public OsEvidenceReport report() {
        return service.generate();
    }
}
