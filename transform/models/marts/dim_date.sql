select
    date_day,
    week_start,
    'Week of ' || to_char(week_start, 'YYYY-MM-DD') as week_label,
    date_trunc('month', date_day)::date as month_start,
    trim(to_char(date_day, 'Month')) as month_name,
    extract(quarter from date_day)::integer as quarter,
    extract(year from date_day)::integer as year,
    extract(dow from date_day)::integer as day_of_week,
    trim(to_char(date_day, 'Day')) as day_name,
    is_weekday
from {{ ref('int_calendar') }}
