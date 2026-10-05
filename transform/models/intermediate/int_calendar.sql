-- Every day in the reporting window. The workbook's DateTable held only days that
-- someone logged time on, so expected hours silently disappeared on any other day.
{%- set calendar_end = var('calendar_end') %}

with bounds as (
    select
        '{{ var("calendar_start") }}'::date as first_day,
        coalesce(
            {{ "'" ~ calendar_end ~ "'" if calendar_end else "null" }}::date,
            (select max(entry_date) from {{ ref('stg_netsuite__time_entries') }})
        ) as last_day
)

select
    day::date as date_day,
    day::date - extract(dow from day)::integer as week_start, -- weeks start on Sunday
    extract(isodow from day) < 6 as is_weekday
from bounds
cross join generate_series(bounds.first_day, bounds.last_day, interval '1 day') as day
