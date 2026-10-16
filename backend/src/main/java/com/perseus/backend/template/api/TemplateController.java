package com.perseus.backend.template.api;

import com.perseus.backend.template.application.TemplateCatalog;
import com.perseus.backend.template.domain.VmTemplate;
import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/templates")
public class TemplateController {

    private final TemplateCatalog templateCatalog;

    public TemplateController(TemplateCatalog templateCatalog) {
        this.templateCatalog = templateCatalog;
    }

    @GetMapping
    public List<VmTemplate> list() {
        return templateCatalog.list();
    }
}
