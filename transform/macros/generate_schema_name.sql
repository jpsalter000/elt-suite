{#- Use each folder's schema (staging, marts, reporting, ...) as-is instead of
    dbt's default "<target schema>_<custom schema>". -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name if custom_schema_name else target.schema }}
{%- endmacro %}
