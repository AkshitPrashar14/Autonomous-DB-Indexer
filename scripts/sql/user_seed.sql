INSERT INTO products (name, category, price)
SELECT
    'Product ' || g,
    CASE g % 10
        WHEN 0 THEN 'Electronics'
        WHEN 1 THEN 'Books'
        WHEN 2 THEN 'Clothing'
        WHEN 3 THEN 'Home'
        WHEN 4 THEN 'Sports'
        WHEN 5 THEN 'Beauty'
        WHEN 6 THEN 'Toys'
        WHEN 7 THEN 'Automotive'
        WHEN 8 THEN 'Food'
        ELSE 'Other'
    END,
    round((10 + random() * 990)::numeric, 2)
FROM generate_series(1, 1000000) g;

INSERT INTO orders
    (customer_id, product_id, status, amount, created_at, updated_at)
SELECT
    1 + floor(random() * 100000)::bigint,
    1 + floor(random() * 1000000)::bigint,
    CASE floor(random() * 5)
        WHEN 0 THEN 'pending'
        WHEN 1 THEN 'processing'
        WHEN 2 THEN 'shipped'
        WHEN 3 THEN 'delivered'
        ELSE 'cancelled'
    END,
    round((10 + random() * 1990)::numeric, 2),
    NOW() - (random() * interval '3 years'),
    NOW() - (random() * interval '3 years')
FROM generate_series(1, 5000000);
