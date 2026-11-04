package com.xenova.backend.common;

import com.fasterxml.jackson.databind.JsonNode;
import java.math.BigInteger;
import java.util.regex.Pattern;

/** Canonical decimal strings used by the Zephyr wire contracts for uint64 values. */
public final class UnsignedDecimal {
    private static final Pattern DECIMAL = Pattern.compile("0|[1-9][0-9]{0,19}");
    private static final String MAX = "18446744073709551615";

    private UnsignedDecimal() {
    }

    public static String read(JsonNode node, String field) {
        String value = node.isTextual() ? node.textValue() : "";
        if (!DECIMAL.matcher(value).matches() || (value.length() == MAX.length() && value.compareTo(MAX) > 0)) {
            throw new IllegalArgumentException("invalid " + field);
        }
        return value;
    }

    public static int compare(String first, String second) {
        int length = Integer.compare(first.length(), second.length());
        return length == 0 ? first.compareTo(second) : length;
    }

    public static Integer elapsedMillis(String start, String end) {
        BigInteger elapsed = new BigInteger(end).subtract(new BigInteger(start));
        return elapsed.signum() >= 0 && elapsed.bitLength() <= 31 ? elapsed.intValue() : null;
    }
}
