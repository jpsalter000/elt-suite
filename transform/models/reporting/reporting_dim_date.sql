-- Published to Power BI as reporting.dim_date.
select
    date_day,
    week_start,
    week_label::text as week_label,
    month_start,
    month_name::text as month_name,
    quarter,
    year,
    day_of_week,
    day_name::text as day_name,
    is_weekday
from {{ ref('dim_date') }}
