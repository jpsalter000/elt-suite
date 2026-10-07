{#- Cross-vendor surrogate key. Native ids collide across vendors and differ in type
    (cus_0001, 5001, HL-000001, UUIDs), so the key hashes the source system too. -#}
{% macro vendor_key(source_system, native_id) -%}
    md5({{ source_system }} || ':' || {{ native_id }})
{%- endmacro %}

{#- The tenant (client company) a vendor's data belongs to, as a SQL literal. -#}
{% macro vendor_tenant(vendor) -%}
    '{{ var('vendors')[vendor]['tenant'] }}'
{%- endmacro %}
