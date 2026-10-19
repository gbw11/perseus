package com.perseus.backend.template.domain;

// guest_kind defaults to linux so a template entry without it keeps working.
public record VmTemplate(
        String templateId,
        String version,
        String displayName,
        int minMemoryMib,
        GuestKind guestKind) {

    public VmTemplate {
        guestKind = guestKind == null ? GuestKind.LINUX : guestKind;
    }
}
