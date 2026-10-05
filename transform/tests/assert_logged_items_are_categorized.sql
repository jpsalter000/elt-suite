{{ config(severity='warn') }}
-- Items people logged time against that the time_categories seed doesn't map.
-- Their hours count toward total hours but not worked time until someone adds them.
select item_name, count(*) as entries, sum(hours) as hours
from {{ ref('int_time_entries__categorized') }}
where category_code = 'unmapped'
group by item_name
