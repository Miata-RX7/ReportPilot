#!/bin/sh
cd /opt/reportpilot
echo "=== 生产：最近的曲线指标 + 存的 calc_formula / type_config ==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -F " | " -c "
SELECT m.id, m.name, m.metric_type, m.created_at,
       coalesce(m.calc_formula::text,'NULL') AS calc_formula,
       coalesce(m.type_config::text,'NULL') AS type_config
FROM metric_definitions m
WHERE m.metric_type='curve'
ORDER BY m.created_at DESC LIMIT 6;"

echo
echo "=== 对比：scalar 指标的 calc_formula（看 agg 存不存得进去）==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -F " | " -c "
SELECT m.id, m.name, m.metric_type, coalesce(m.calc_formula::text,'NULL')
FROM metric_definitions m
WHERE m.metric_type='scalar'
ORDER BY m.created_at DESC LIMIT 4;"

echo
echo "=== 库里 agg 的分布（看有没有 max 的曲线）==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -F " | " -c "
SELECT m.metric_type, m.calc_formula->>'agg' AS agg, count(*)
FROM metric_definitions m
WHERE m.calc_formula IS NOT NULL
GROUP BY 1,2 ORDER BY 1,2;"
