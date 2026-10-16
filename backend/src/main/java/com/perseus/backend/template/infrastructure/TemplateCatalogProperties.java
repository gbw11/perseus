package com.perseus.backend.template.infrastructure;

import com.perseus.backend.template.domain.VmTemplate;
import java.util.List;
import org.springframework.boot.context.properties.ConfigurationProperties;

// First-stage catalog fixed in configuration until the platform team publishes the real template list.
@ConfigurationProperties(prefix = "perseus.template-catalog")
public record TemplateCatalogProperties(List<VmTemplate> templates) {

    public TemplateCatalogProperties {
        templates = templates == null ? List.of() : List.copyOf(templates);
    }
}
