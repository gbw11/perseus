package com.xenova.backend.template.application;

import com.xenova.backend.template.domain.VmTemplate;
import com.xenova.backend.template.infrastructure.TemplateCatalogProperties;
import java.util.List;
import java.util.Optional;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.stereotype.Service;

@Service
@EnableConfigurationProperties(TemplateCatalogProperties.class)
public class TemplateCatalog {

    private final List<VmTemplate> templates;

    public TemplateCatalog(TemplateCatalogProperties properties) {
        this.templates = properties.templates();
    }

    public List<VmTemplate> list() {
        return templates;
    }

    public Optional<VmTemplate> find(String templateId, String version) {
        return templates.stream()
                .filter(template -> template.templateId().equals(templateId) && template.version().equals(version))
                .findFirst();
    }
}
