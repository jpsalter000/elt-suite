{#- Power BI connects as "powerbi", a login role that only inherits reporting_reader.
    Its password is set once by an administrator (ALTER ROLE powerbi PASSWORD ...), never here,
    so it can't end up in dbt's logs. -#}
{% macro create_reporting_roles() %}
do $$
begin
    if not exists (select from pg_roles where rolname = 'reporting_reader') then
        create role reporting_reader nologin;
    end if;
    if not exists (select from pg_roles where rolname = 'powerbi') then
        create role powerbi login in role reporting_reader;
    end if;
end
$$;
{% endmacro %}

{#- A selective build (e.g. only the vendor models) may not create the reporting schema. -#}
{% macro grant_reporting_usage() %}
{%- if execute %}
do $$
begin
    if exists (select from pg_namespace where nspname = 'reporting') then
        grant usage on schema reporting to reporting_reader;
    end if;
end
$$;
{%- endif %}
{% endmacro %}
