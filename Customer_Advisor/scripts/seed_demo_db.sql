-- =============================================================================
-- Cashy Demo Database Seed Script
-- US Freelancer Persona — English Language
--
-- Usage:
--   createdb -U postgres cashy_banking
--   psql -U postgres -d cashy_banking -f scripts/seed_demo_db.sql
--
-- To reset: dropdb -U postgres cashy_banking && createdb -U postgres cashy_banking && psql -U postgres -d cashy_banking -f scripts/seed_demo_db.sql
-- =============================================================================

-- =============================================================================
-- 1. TRIGGER FUNCTION
-- =============================================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- 2. TABLES (11)
-- =============================================================================

-- account_types
CREATE TABLE account_types (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER trigger_account_types_updated_at
    BEFORE UPDATE ON account_types FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- accounts
CREATE TABLE accounts (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    account_type_id INTEGER NOT NULL REFERENCES account_types(id),
    current_balance NUMERIC(15,2) NOT NULL DEFAULT 0.00,
    is_active BOOLEAN NOT NULL DEFAULT true,
    account_number VARCHAR(50),
    institution VARCHAR(100),
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    credit_limit NUMERIC(15,2) DEFAULT NULL,
    opening_balance NUMERIC(15,2) NOT NULL DEFAULT 0.00,
    CONSTRAINT unique_account_name UNIQUE (name)
);
CREATE INDEX idx_accounts_name ON accounts(name);
CREATE INDEX idx_accounts_type ON accounts(account_type_id);
CREATE INDEX idx_accounts_active ON accounts(is_active);
CREATE TRIGGER trigger_accounts_updated_at
    BEFORE UPDATE ON accounts FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- categories
CREATE TABLE categories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    parent_category_id INTEGER REFERENCES categories(id),
    category_type VARCHAR(20) NOT NULL,
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_categories_type ON categories(category_type);
CREATE INDEX idx_categories_parent ON categories(parent_category_id);
CREATE TRIGGER trigger_categories_updated_at
    BEFORE UPDATE ON categories FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- transactions
CREATE TABLE transactions (
    id SERIAL PRIMARY KEY,
    transaction_date DATE NOT NULL,
    description VARCHAR(500) NOT NULL,
    transaction_type VARCHAR(20) NOT NULL,
    total_amount NUMERIC(15,2) NOT NULL,
    reference_number VARCHAR(100),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT transactions_transaction_type_check
        CHECK (transaction_type IN ('income', 'expense', 'transfer', 'adjustment')),
    CONSTRAINT adjustment_requires_notes
        CHECK (transaction_type <> 'adjustment' OR notes IS NOT NULL)
);
CREATE INDEX idx_transactions_date ON transactions(transaction_date);
CREATE INDEX idx_transactions_type ON transactions(transaction_type);
CREATE INDEX idx_transactions_date_type ON transactions(transaction_date, transaction_type);
CREATE TRIGGER trigger_transactions_updated_at
    BEFORE UPDATE ON transactions FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- transaction_entries
CREATE TABLE transaction_entries (
    id SERIAL PRIMARY KEY,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    category_id INTEGER REFERENCES categories(id),
    amount NUMERIC(15,2) NOT NULL,
    entry_type VARCHAR(10) NOT NULL,
    description VARCHAR(500),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_entries_transaction ON transaction_entries(transaction_id);
CREATE INDEX idx_entries_account ON transaction_entries(account_id);
CREATE INDEX idx_entries_category ON transaction_entries(category_id);

-- budgets
CREATE TABLE budgets (
    id SERIAL PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    month_year DATE NOT NULL,
    budgeted_amount NUMERIC(15,2) NOT NULL,
    actual_amount NUMERIC(15,2) DEFAULT 0.00,
    variance_amount NUMERIC(15,2) GENERATED ALWAYS AS (actual_amount - budgeted_amount) STORED,
    variance_percentage NUMERIC(5,2) GENERATED ALWAYS AS (
        CASE WHEN budgeted_amount = 0 THEN 0
             ELSE ROUND((actual_amount - budgeted_amount) / budgeted_amount * 100, 2)
        END
    ) STORED,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_category_month UNIQUE (category_id, month_year)
);
CREATE INDEX idx_budgets_month ON budgets(month_year);
CREATE INDEX idx_budgets_category ON budgets(category_id);
CREATE INDEX idx_budgets_category_month ON budgets(category_id, month_year);
CREATE TRIGGER trigger_budgets_updated_at
    BEFORE UPDATE ON budgets FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- budget_goals
CREATE TABLE budget_goals (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    target_amount NUMERIC(15,2) NOT NULL,
    current_amount NUMERIC(15,2) NOT NULL DEFAULT 0.00,
    target_date DATE,
    goal_type VARCHAR(20) NOT NULL,
    priority INTEGER,
    is_active BOOLEAN NOT NULL DEFAULT true,
    completion_percentage NUMERIC(5,2) GENERATED ALWAYS AS (
        CASE WHEN target_amount = 0 THEN 0
             ELSE ROUND(current_amount / target_amount * 100, 2)
        END
    ) STORED,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT budget_goals_goal_type_check
        CHECK (goal_type IN ('emergency_fund', 'savings', 'debt_payoff', 'investment', 'purchase', 'other')),
    CONSTRAINT budget_goals_priority_check CHECK (priority >= 1 AND priority <= 10)
);
CREATE INDEX idx_budget_goals_active ON budget_goals(is_active);
CREATE INDEX idx_budget_goals_type ON budget_goals(goal_type);
CREATE INDEX idx_budget_goals_target_date ON budget_goals(target_date);
CREATE TRIGGER trigger_budget_goals_updated_at
    BEFORE UPDATE ON budget_goals FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- goal_contributions
CREATE TABLE goal_contributions (
    id SERIAL PRIMARY KEY,
    goal_id INTEGER NOT NULL REFERENCES budget_goals(id) ON DELETE CASCADE,
    month_year DATE NOT NULL,
    planned_amount NUMERIC(15,2) NOT NULL DEFAULT 0.00,
    actual_amount NUMERIC(15,2) NOT NULL DEFAULT 0.00,
    source_account_id INTEGER REFERENCES accounts(id),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT goal_contributions_unique_goal_month UNIQUE (goal_id, month_year),
    CONSTRAINT goal_contributions_planned_amount_check CHECK (planned_amount >= 0),
    CONSTRAINT goal_contributions_actual_amount_check CHECK (actual_amount >= 0)
);
CREATE INDEX idx_goal_contributions_goal ON goal_contributions(goal_id);
CREATE INDEX idx_goal_contributions_month ON goal_contributions(month_year);
CREATE INDEX idx_goal_contributions_goal_month ON goal_contributions(goal_id, month_year);
CREATE INDEX idx_goal_contributions_source_account ON goal_contributions(source_account_id);
CREATE TRIGGER trigger_goal_contributions_updated_at
    BEFORE UPDATE ON goal_contributions FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- monthly_balances
CREATE TABLE monthly_balances (
    id SERIAL PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    month_year DATE NOT NULL,
    opening_balance NUMERIC(15,2) NOT NULL,
    closing_balance NUMERIC(15,2) NOT NULL,
    net_change NUMERIC(15,2) GENERATED ALWAYS AS (closing_balance - opening_balance) STORED,
    total_deposits NUMERIC(15,2) DEFAULT 0.00,
    total_withdrawals NUMERIC(15,2) DEFAULT 0.00,
    average_daily_balance NUMERIC(15,2),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_account_month UNIQUE (account_id, month_year)
);
CREATE INDEX idx_monthly_balances_account ON monthly_balances(account_id);
CREATE INDEX idx_monthly_balances_month ON monthly_balances(month_year);

-- spending_patterns
CREATE TABLE spending_patterns (
    id SERIAL PRIMARY KEY,
    category_id INTEGER REFERENCES categories(id),
    account_id INTEGER REFERENCES accounts(id),
    month_year DATE NOT NULL,
    pattern_type VARCHAR(50) NOT NULL,
    average_amount NUMERIC(15,2),
    frequency_count INTEGER,
    confidence_score NUMERIC(3,2),
    pattern_data JSONB,
    insights TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT spending_patterns_pattern_type_check
        CHECK (pattern_type IN ('weekly', 'monthly', 'seasonal', 'trending_up', 'trending_down', 'volatile', 'stable')),
    CONSTRAINT spending_patterns_confidence_score_check CHECK (confidence_score >= 0.00 AND confidence_score <= 1.00)
);
CREATE INDEX idx_spending_patterns_category ON spending_patterns(category_id);
CREATE INDEX idx_spending_patterns_month ON spending_patterns(month_year);
CREATE INDEX idx_spending_patterns_type ON spending_patterns(pattern_type);

-- financial_rules
CREATE TABLE financial_rules (
    id SERIAL PRIMARY KEY,
    rule_name VARCHAR(200) NOT NULL,
    rule_type VARCHAR(50) NOT NULL,
    parameters JSONB NOT NULL,
    conditions JSONB,
    is_active BOOLEAN NOT NULL DEFAULT true,
    priority INTEGER DEFAULT 5,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT financial_rules_rule_type_check
        CHECK (rule_type IN ('budget_alert', 'spending_limit', 'savings_target', 'investment_allocation', 'debt_warning', 'cash_flow')),
    CONSTRAINT financial_rules_priority_check CHECK (priority >= 1 AND priority <= 10)
);
CREATE INDEX idx_financial_rules_active ON financial_rules(is_active);
CREATE INDEX idx_financial_rules_type ON financial_rules(rule_type);

-- =============================================================================
-- 3. VIEWS (8)
-- =============================================================================

CREATE VIEW v_transaction_details AS
SELECT
    t.id AS transaction_id,
    t.transaction_date,
    t.description AS transaction_description,
    t.transaction_type,
    t.total_amount,
    te.id AS entry_id,
    a.name AS account_name,
    a.institution,
    c.name AS category_name,
    pc.name AS parent_category_name,
    te.amount AS entry_amount,
    te.entry_type,
    te.description AS entry_description,
    t.notes,
    t.created_at
FROM transactions t
JOIN transaction_entries te ON t.id = te.transaction_id
JOIN accounts a ON te.account_id = a.id
LEFT JOIN categories c ON te.category_id = c.id
LEFT JOIN categories pc ON c.parent_category_id = pc.id
ORDER BY t.transaction_date DESC, t.id DESC, te.id;

CREATE VIEW v_monthly_spending AS
SELECT
    date_trunc('month', t.transaction_date::timestamp with time zone) AS month_year,
    c.name AS category,
    c.category_type,
    SUM(te.amount) AS total_amount,
    COUNT(*) AS transaction_count,
    AVG(te.amount) AS average_amount
FROM transactions t
JOIN transaction_entries te ON t.id = te.transaction_id
LEFT JOIN categories c ON te.category_id = c.id
WHERE t.transaction_type = 'expense'
GROUP BY date_trunc('month', t.transaction_date::timestamp with time zone), c.name, c.category_type
ORDER BY date_trunc('month', t.transaction_date::timestamp with time zone) DESC, SUM(te.amount) DESC;

CREATE VIEW v_account_summary AS
SELECT
    a.id,
    a.name,
    at.name AS account_type,
    a.current_balance,
    a.currency,
    a.is_active,
    COALESCE(mb.closing_balance, a.current_balance) AS last_month_balance,
    a.current_balance - COALESCE(mb.closing_balance, a.current_balance) AS monthly_change
FROM accounts a
LEFT JOIN account_types at ON a.account_type_id = at.id
LEFT JOIN monthly_balances mb ON a.id = mb.account_id
    AND mb.month_year = date_trunc('month', CURRENT_DATE - INTERVAL '1 month')
WHERE a.is_active = true;

CREATE VIEW v_category_hierarchy AS
WITH RECURSIVE category_path AS (
    SELECT id, name, parent_category_id, category_type,
           name::text AS full_path, 1 AS level
    FROM categories
    WHERE parent_category_id IS NULL
    UNION ALL
    SELECT c.id, c.name, c.parent_category_id, c.category_type,
           (cp.full_path || ' > ' || c.name::text) AS full_path,
           cp.level + 1
    FROM categories c
    JOIN category_path cp ON c.parent_category_id = cp.id
)
SELECT id, name, parent_category_id, category_type, full_path, level
FROM category_path;

CREATE VIEW v_credit_utilization AS
SELECT
    id, name, current_balance, credit_limit,
    ABS(current_balance) AS debt_owed,
    credit_limit - ABS(current_balance) AS available_credit,
    CASE WHEN credit_limit > 0
         THEN ROUND(ABS(current_balance) / credit_limit * 100, 2)
         ELSE 0
    END AS utilization_percentage
FROM accounts a
WHERE account_type_id = (SELECT id FROM account_types WHERE name = 'Credit Card')
  AND is_active = true;

CREATE VIEW v_goal_progress_summary AS
SELECT
    g.id, g.name, g.description, g.goal_type, g.target_amount, g.current_amount,
    g.target_date, g.priority, g.is_active, g.completion_percentage,
    COALESCE(SUM(gc.planned_amount), 0) AS total_planned_contributions,
    COALESCE(SUM(gc.actual_amount), 0) AS total_actual_contributions,
    g.target_amount - g.current_amount AS amount_remaining,
    CASE WHEN g.target_date IS NOT NULL
         THEN EXTRACT(MONTH FROM AGE(g.target_date, CURRENT_DATE))
         ELSE NULL
    END AS months_remaining,
    CASE WHEN g.target_date IS NOT NULL
              AND EXTRACT(MONTH FROM AGE(g.target_date, CURRENT_DATE)) > 0
         THEN ROUND((g.target_amount - g.current_amount) /
              EXTRACT(MONTH FROM AGE(g.target_date, CURRENT_DATE)), 2)
         ELSE NULL
    END AS required_monthly_contribution
FROM budget_goals g
LEFT JOIN goal_contributions gc ON g.id = gc.goal_id
GROUP BY g.id, g.name, g.description, g.goal_type, g.target_amount,
         g.current_amount, g.target_date, g.priority, g.is_active, g.completion_percentage;

CREATE VIEW v_monthly_contribution_summary AS
SELECT
    gc.month_year,
    g.name AS goal_name,
    g.goal_type,
    gc.planned_amount,
    gc.actual_amount,
    gc.actual_amount - gc.planned_amount AS variance,
    CASE WHEN gc.planned_amount > 0
         THEN ROUND(gc.actual_amount / gc.planned_amount * 100, 2)
         ELSE 0
    END AS completion_percentage,
    a.name AS source_account_name,
    at.name AS source_account_type,
    gc.notes,
    gc.created_at
FROM goal_contributions gc
JOIN budget_goals g ON gc.goal_id = g.id
LEFT JOIN accounts a ON gc.source_account_id = a.id
LEFT JOIN account_types at ON a.account_type_id = at.id
ORDER BY gc.month_year DESC, g.name;

-- =============================================================================
-- 4. SEED DATA
-- =============================================================================

-- 4a. Account Types (6 rows)
INSERT INTO account_types (id, name, description) VALUES
(1, 'Bank Account',    'Standard checking and business accounts'),
(2, 'Investment',      'Investment accounts and portfolios'),
(3, 'Credit Card',     'Credit card accounts'),
(4, 'Cash',            'Physical cash on hand'),
(5, 'Loan',            'Loan accounts'),
(6, 'Savings Account', 'High-yield savings accounts');
SELECT setval('account_types_id_seq', 6);

-- 4b. Categories (35 rows with hierarchy)
-- Parent expense categories
INSERT INTO categories (id, name, parent_category_id, category_type, description) VALUES
( 1, 'Housing',          NULL, 'expense', 'Housing and rent expenses'),
( 2, 'Food & Dining',    NULL, 'expense', 'Groceries and restaurants'),
( 3, 'Transportation',   NULL, 'expense', 'Getting around'),
( 4, 'Business',         NULL, 'expense', 'Business operating expenses'),
( 5, 'Health & Fitness', NULL, 'expense', 'Health insurance and fitness'),
( 6, 'Entertainment',    NULL, 'expense', 'Fun and leisure'),
( 7, 'Subscriptions',    NULL, 'expense', 'Recurring subscriptions'),
( 8, 'Utilities',        NULL, 'expense', 'Utility bills'),
( 9, 'Personal Care',    NULL, 'expense', 'Personal care and grooming'),
(10, 'Education',        NULL, 'expense', 'Learning and courses');

-- Child expense categories
INSERT INTO categories (id, name, parent_category_id, category_type, description) VALUES
(11, 'Rent',                  1, 'expense', 'Monthly rent payment'),
(12, 'Groceries',             2, 'expense', 'Grocery shopping'),
(13, 'Dining Out',            2, 'expense', 'Restaurants and takeout'),
(14, 'Coffee Shops',          2, 'expense', 'Coffee and cafe visits'),
(15, 'Uber/Lyft',             3, 'expense', 'Rideshare services'),
(16, 'Gas',                   3, 'expense', 'Fuel for car'),
(17, 'Software Subscriptions', 4, 'expense', 'SaaS tools for work'),
(18, 'Coworking',             4, 'expense', 'Coworking space membership'),
(19, 'Equipment',             4, 'expense', 'Hardware and office equipment'),
(20, 'Contractors',           4, 'expense', 'Subcontractor payments'),
(21, 'Marketing',             4, 'expense', 'Advertising and promotion'),
(22, 'Legal & Accounting',    4, 'expense', 'Professional services'),
(23, 'Health Insurance',      5, 'expense', 'Monthly health insurance premium'),
(24, 'Gym',                   5, 'expense', 'Gym membership'),
(25, 'Streaming',             7, 'expense', 'Netflix, Spotify, etc.'),
(26, 'Cloud Services',        7, 'expense', 'AWS, hosting, domains'),
(27, 'Internet',              8, 'expense', 'Home internet service'),
(28, 'Phone',                 8, 'expense', 'Mobile phone plan'),
(29, 'Electricity',           8, 'expense', 'Electric bill');

-- Income categories
INSERT INTO categories (id, name, parent_category_id, category_type, description) VALUES
(30, 'Client Invoices',      NULL, 'income', 'One-time client project payments'),
(31, 'Recurring Retainers',  NULL, 'income', 'Monthly retainer clients'),
(32, 'Affiliate Income',     NULL, 'income', 'Affiliate and referral commissions'),
(33, 'Interest',             NULL, 'income', 'Interest earned on savings');

-- Transfer categories
INSERT INTO categories (id, name, parent_category_id, category_type, description) VALUES
(34, 'Internal Transfer',    NULL, 'transfer', 'Transfers between own accounts'),
(35, 'Credit Card Payment',  NULL, 'transfer', 'Paying off credit card balance');

SELECT setval('categories_id_seq', 35);

-- 4c. Accounts (11 rows)
INSERT INTO accounts (id, name, account_type_id, current_balance, is_active, account_number, institution, currency, credit_limit, opening_balance) VALUES
( 1, 'Chase Personal Checking', 1,     0.00, true, '****4521', 'Chase',    'USD', NULL,    8000.00),
( 2, 'Chase Business Checking', 1,     0.00, true, '****8903', 'Chase',    'USD', NULL,    3000.00),
( 3, 'PayPal',                  1,     0.00, true, NULL,       'PayPal',   'USD', NULL,       0.00),
( 4, 'Stripe',                  1,     0.00, true, NULL,       'Stripe',   'USD', NULL,       0.00),
( 5, 'Wise',                    1,     0.00, true, NULL,       'Wise',     'USD', NULL,       0.00),
( 6, 'High-Yield Savings',      6,     0.00, true, '****7712', 'Marcus',   'USD', NULL,   10000.00),
( 7, 'Cash',                    4,     0.00, true, NULL,        NULL,      'USD', NULL,     500.00),
( 8, 'Chase Sapphire',          3,     0.00, true, '****3344', 'Chase',    'USD', 10000.00,  0.00),
( 9, 'Amex Blue',               3,     0.00, true, '****2211', 'Amex',     'USD',  5000.00,  0.00),
(10, 'Fidelity Brokerage',      2,     0.00, true, '****9988', 'Fidelity', 'USD', NULL,   20000.00),
(11, 'Fidelity Roth IRA',       2,     0.00, true, '****5566', 'Fidelity', 'USD', NULL,    8000.00);
SELECT setval('accounts_id_seq', 11);

-- =============================================================================
-- 4d. Transactions + Entries
-- Covering Oct 2025 – Jan 2026 (4 months)
-- =============================================================================

-- Helper: We'll use explicit IDs for transactions so entries can reference them.

-- ===================== OCTOBER 2025 =====================

-- Income
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(1, '2025-10-03', 'Client: Riverside Marketing - Website Redesign', 'income', 5500.00),
(2, '2025-10-10', 'Client: TechStart Inc - Monthly Retainer', 'income', 1500.00),
(3, '2025-10-15', 'Client: GreenLeaf Co - E-commerce Store', 'income', 3200.00),
(4, '2025-10-20', 'Client: DataFlow Systems - API Integration', 'income', 4000.00),
(5, '2025-10-25', 'Client: BlueSky Media - Monthly Retainer', 'income', 1500.00);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(1, 4, 30, 5500.00, 'credit', 'Stripe deposit - Riverside Marketing'),
(2, 2, 31, 1500.00, 'credit', 'Direct deposit - TechStart retainer'),
(3, 3, 30, 3200.00, 'credit', 'PayPal deposit - GreenLeaf Co'),
(4, 4, 30, 4000.00, 'credit', 'Stripe deposit - DataFlow Systems'),
(5, 2, 31, 1500.00, 'credit', 'Direct deposit - BlueSky retainer');

-- Expenses - Housing & Utilities
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(6,  '2025-10-01', 'October Rent', 'expense', 1800.00),
(7,  '2025-10-05', 'Internet - Comcast', 'expense', 79.99),
(8,  '2025-10-08', 'Phone - T-Mobile', 'expense', 55.00),
(9,  '2025-10-12', 'Electric Bill - ConEd', 'expense', 95.00);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(6,  1, 11, 1800.00, 'debit', 'Rent payment'),
(7,  1, 27, 79.99,   'debit', 'Internet service'),
(8,  1, 28, 55.00,   'debit', 'Mobile plan'),
(9,  1, 29, 95.00,   'debit', 'Electric bill');

-- Expenses - Business
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(10, '2025-10-01', 'Figma - Pro Plan', 'expense', 15.00),
(11, '2025-10-01', 'GitHub - Team Plan', 'expense', 25.00),
(12, '2025-10-01', 'Vercel - Pro Hosting', 'expense', 20.00),
(13, '2025-10-01', 'Google Workspace', 'expense', 12.00),
(14, '2025-10-01', 'WeWork - Hot Desk', 'expense', 350.00),
(15, '2025-10-15', 'Contractor: Sarah - Design Work', 'expense', 800.00),
(16, '2025-10-20', 'AWS Monthly', 'expense', 45.50);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(10, 9, 17, 15.00,   'debit', 'Figma subscription'),
(11, 9, 17, 25.00,   'debit', 'GitHub subscription'),
(12, 9, 17, 20.00,   'debit', 'Vercel hosting'),
(13, 9, 17, 12.00,   'debit', 'Google Workspace'),
(14, 2, 18, 350.00,  'debit', 'Coworking monthly'),
(15, 2, 20, 800.00,  'debit', 'Sarah - UI design for Riverside'),
(16, 9, 26, 45.50,   'debit', 'AWS hosting');

-- Expenses - Food & Dining
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(17, '2025-10-02', 'Whole Foods', 'expense', 87.30),
(18, '2025-10-06', 'Trader Joes', 'expense', 62.15),
(19, '2025-10-09', 'Chipotle', 'expense', 14.50),
(20, '2025-10-13', 'Whole Foods', 'expense', 95.20),
(21, '2025-10-16', 'Thai Basil Restaurant', 'expense', 42.00),
(22, '2025-10-18', 'Starbucks', 'expense', 6.75),
(23, '2025-10-22', 'Trader Joes', 'expense', 58.40),
(24, '2025-10-25', 'Shake Shack', 'expense', 18.90),
(25, '2025-10-28', 'Whole Foods', 'expense', 78.60),
(26, '2025-10-30', 'Blue Bottle Coffee', 'expense', 5.50);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(17, 8, 12, 87.30,   'debit', 'Weekly groceries'),
(18, 8, 12, 62.15,   'debit', 'Weekly groceries'),
(19, 8, 13, 14.50,   'debit', 'Lunch'),
(20, 8, 12, 95.20,   'debit', 'Weekly groceries'),
(21, 8, 13, 42.00,   'debit', 'Dinner out'),
(22, 7, 14, 6.75,    'debit', 'Morning coffee'),
(23, 8, 12, 58.40,   'debit', 'Weekly groceries'),
(24, 8, 13, 18.90,   'debit', 'Lunch out'),
(25, 8, 12, 78.60,   'debit', 'Weekly groceries'),
(26, 7, 14, 5.50,    'debit', 'Coffee');

-- Expenses - Health & Entertainment
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(27, '2025-10-01', 'Blue Cross - Health Insurance', 'expense', 450.00),
(28, '2025-10-01', 'Equinox Gym', 'expense', 95.00),
(29, '2025-10-05', 'Netflix', 'expense', 15.49),
(30, '2025-10-05', 'Spotify', 'expense', 10.99),
(31, '2025-10-14', 'Movie Theater - AMC', 'expense', 22.00),
(32, '2025-10-22', 'Concert Tickets - MSG', 'expense', 120.00);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(27, 1, 23, 450.00,  'debit', 'Monthly health premium'),
(28, 1, 24, 95.00,   'debit', 'Gym membership'),
(29, 1, 25, 15.49,   'debit', 'Netflix subscription'),
(30, 1, 25, 10.99,   'debit', 'Spotify subscription'),
(31, 8, 6,  22.00,   'debit', 'Movie tickets'),
(32, 8, 6,  120.00,  'debit', 'Concert');

-- Expenses - Transportation
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(33, '2025-10-07', 'Uber to client meeting', 'expense', 28.50),
(34, '2025-10-19', 'Lyft to airport', 'expense', 45.00);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(33, 8, 15, 28.50,   'debit', 'Uber ride'),
(34, 8, 15, 45.00,   'debit', 'Lyft ride');

-- Transfers - October
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(35, '2025-10-05', 'Transfer to savings', 'transfer', 1000.00),
(36, '2025-10-15', 'Investment contribution', 'transfer', 500.00),
(37, '2025-10-15', 'Roth IRA contribution', 'transfer', 500.00),
(38, '2025-10-28', 'Chase Sapphire payment', 'transfer', 500.00),
(39, '2025-10-10', 'PayPal to Business Checking', 'transfer', 3200.00);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(35, 1, NULL, 1000.00, 'debit',  'To savings'),
(35, 6, NULL, 1000.00, 'credit', 'From checking'),
(36, 1, NULL, 500.00,  'debit',  'To brokerage'),
(36, 10, NULL, 500.00, 'credit', 'Monthly investment'),
(37, 1, NULL, 500.00,  'debit',  'To Roth IRA'),
(37, 11, NULL, 500.00, 'credit', 'Monthly Roth contribution'),
(38, 1, NULL, 500.00,  'debit',  'CC payment'),
(38, 8, NULL, 500.00,  'credit', 'Payment received'),
(39, 3, NULL, 3200.00, 'debit',  'Withdraw to bank'),
(39, 2, NULL, 3200.00, 'credit', 'From PayPal');

-- Monthly transfer: business to personal for living expenses
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(200, '2025-10-02', 'Transfer from Business to Personal', 'transfer', 5000.00);
INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(200, 2, NULL, 5000.00, 'debit',  'To personal checking'),
(200, 1, NULL, 5000.00, 'credit', 'From business checking');

-- ===================== NOVEMBER 2025 =====================

-- Income
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(40, '2025-11-05', 'Client: TechStart Inc - Monthly Retainer', 'income', 1500.00),
(41, '2025-11-07', 'Client: Artisan Bakery - New Website', 'income', 2800.00),
(42, '2025-11-12', 'Client: DataFlow Systems - Phase 2', 'income', 6000.00),
(43, '2025-11-20', 'Client: BlueSky Media - Monthly Retainer', 'income', 1500.00),
(44, '2025-11-25', 'Affiliate Commission - WPEngine', 'income', 350.00),
(45, '2025-11-28', 'Client: Peak Performance - Landing Page', 'income', 1800.00);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(40, 2,  31, 1500.00, 'credit', 'Direct deposit - TechStart retainer'),
(41, 3,  30, 2800.00, 'credit', 'PayPal deposit - Artisan Bakery'),
(42, 4,  30, 6000.00, 'credit', 'Stripe deposit - DataFlow Systems'),
(43, 2,  31, 1500.00, 'credit', 'Direct deposit - BlueSky retainer'),
(44, 3,  32, 350.00,  'credit', 'WPEngine affiliate payout'),
(45, 5,  30, 1800.00, 'credit', 'Wise deposit - Peak Performance (UK client)');

-- Expenses - Housing & Utilities (Nov)
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(46, '2025-11-01', 'November Rent', 'expense', 1800.00),
(47, '2025-11-05', 'Internet - Comcast', 'expense', 79.99),
(48, '2025-11-08', 'Phone - T-Mobile', 'expense', 55.00),
(49, '2025-11-15', 'Electric Bill - ConEd', 'expense', 110.00);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(46, 1, 11, 1800.00, 'debit', 'Rent payment'),
(47, 1, 27, 79.99,   'debit', 'Internet service'),
(48, 1, 28, 55.00,   'debit', 'Mobile plan'),
(49, 1, 29, 110.00,  'debit', 'Electric bill');

-- Expenses - Business (Nov)
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(50, '2025-11-01', 'Figma - Pro Plan', 'expense', 15.00),
(51, '2025-11-01', 'GitHub - Team Plan', 'expense', 25.00),
(52, '2025-11-01', 'Vercel - Pro Hosting', 'expense', 20.00),
(53, '2025-11-01', 'Google Workspace', 'expense', 12.00),
(54, '2025-11-01', 'WeWork - Hot Desk', 'expense', 350.00),
(55, '2025-11-10', 'Contractor: Mike - Backend Dev', 'expense', 1200.00),
(56, '2025-11-18', 'AWS Monthly', 'expense', 52.30),
(57, '2025-11-20', 'Adobe Creative Cloud', 'expense', 54.99),
(58, '2025-11-22', 'Notion - Team Plan', 'expense', 10.00);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(50, 9,  17, 15.00,   'debit', 'Figma subscription'),
(51, 9,  17, 25.00,   'debit', 'GitHub subscription'),
(52, 9,  17, 20.00,   'debit', 'Vercel hosting'),
(53, 9,  17, 12.00,   'debit', 'Google Workspace'),
(54, 2,  18, 350.00,  'debit', 'Coworking monthly'),
(55, 2,  20, 1200.00, 'debit', 'Mike - backend for DataFlow'),
(56, 9,  26, 52.30,   'debit', 'AWS hosting'),
(57, 9,  17, 54.99,   'debit', 'Adobe CC'),
(58, 9,  17, 10.00,   'debit', 'Notion subscription');

-- Expenses - Food & Dining (Nov)
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(59, '2025-11-02', 'Whole Foods', 'expense', 92.40),
(60, '2025-11-06', 'Trader Joes', 'expense', 55.80),
(61, '2025-11-09', 'Sushi Nakazawa', 'expense', 85.00),
(62, '2025-11-13', 'Whole Foods', 'expense', 88.10),
(63, '2025-11-17', 'Panda Express', 'expense', 12.50),
(64, '2025-11-19', 'Starbucks', 'expense', 7.25),
(65, '2025-11-23', 'Trader Joes', 'expense', 67.30),
(66, '2025-11-26', 'Thanksgiving Groceries - Whole Foods', 'expense', 145.00),
(67, '2025-11-28', 'Blue Bottle Coffee', 'expense', 5.50),
(68, '2025-11-30', 'Sweetgreen', 'expense', 16.50);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(59, 8,  12, 92.40,   'debit', 'Weekly groceries'),
(60, 8,  12, 55.80,   'debit', 'Weekly groceries'),
(61, 8,  13, 85.00,   'debit', 'Dinner out'),
(62, 8,  12, 88.10,   'debit', 'Weekly groceries'),
(63, 8,  13, 12.50,   'debit', 'Lunch'),
(64, 7,  14, 7.25,    'debit', 'Morning coffee'),
(65, 8,  12, 67.30,   'debit', 'Weekly groceries'),
(66, 8,  12, 145.00,  'debit', 'Thanksgiving groceries'),
(67, 7,  14, 5.50,    'debit', 'Coffee'),
(68, 8,  13, 16.50,   'debit', 'Lunch');

-- Expenses - Health & Entertainment (Nov)
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(69, '2025-11-01', 'Blue Cross - Health Insurance', 'expense', 450.00),
(70, '2025-11-01', 'Equinox Gym', 'expense', 95.00),
(71, '2025-11-05', 'Netflix', 'expense', 15.49),
(72, '2025-11-05', 'Spotify', 'expense', 10.99),
(73, '2025-11-15', 'Broadway Show', 'expense', 150.00),
(74, '2025-11-22', 'Video Game - Steam', 'expense', 39.99);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(69, 1, 23, 450.00,  'debit', 'Monthly health premium'),
(70, 1, 24, 95.00,   'debit', 'Gym membership'),
(71, 1, 25, 15.49,   'debit', 'Netflix subscription'),
(72, 1, 25, 10.99,   'debit', 'Spotify subscription'),
(73, 8, 6,  150.00,  'debit', 'Broadway tickets'),
(74, 1, 6,  39.99,   'debit', 'Steam purchase');

-- Expenses - Transportation (Nov)
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(75, '2025-11-03', 'Uber to coworking', 'expense', 15.00),
(76, '2025-11-14', 'Lyft to client dinner', 'expense', 22.50);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(75, 8, 15, 15.00,   'debit', 'Uber ride'),
(76, 8, 15, 22.50,   'debit', 'Lyft ride');

-- Transfers - November
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(77, '2025-11-05', 'Transfer to savings', 'transfer', 1500.00),
(78, '2025-11-15', 'Investment contribution', 'transfer', 500.00),
(79, '2025-11-15', 'Roth IRA contribution', 'transfer', 500.00),
(80, '2025-11-25', 'Amex Blue payment', 'transfer', 300.00),
(81, '2025-11-08', 'Stripe to Business Checking', 'transfer', 6000.00),
(82, '2025-11-26', 'Wise to Business Checking', 'transfer', 1800.00);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(77, 1, NULL,  1500.00, 'debit',  'To savings'),
(77, 6, NULL,  1500.00, 'credit', 'From checking'),
(78, 1, NULL,  500.00,  'debit',  'To brokerage'),
(78, 10, NULL, 500.00,  'credit', 'Monthly investment'),
(79, 1, NULL,  500.00,  'debit',  'To Roth IRA'),
(79, 11, NULL, 500.00,  'credit', 'Monthly Roth contribution'),
(80, 2, NULL,  300.00,  'debit',  'CC payment'),
(80, 9, NULL,  300.00,  'credit', 'Payment received'),
(81, 4, NULL,  6000.00, 'debit',  'Withdraw to bank'),
(81, 2, NULL,  6000.00, 'credit', 'From Stripe'),
(82, 5, NULL,  1800.00, 'debit',  'Withdraw to bank'),
(82, 2, NULL,  1800.00, 'credit', 'From Wise');

-- Monthly transfer: business to personal for living expenses
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(201, '2025-11-02', 'Transfer from Business to Personal', 'transfer', 5000.00);
INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(201, 2, NULL, 5000.00, 'debit',  'To personal checking'),
(201, 1, NULL, 5000.00, 'credit', 'From business checking');

-- ===================== DECEMBER 2025 =====================

-- Income
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(83,  '2025-12-03', 'Client: TechStart Inc - Monthly Retainer', 'income', 1500.00),
(84,  '2025-12-05', 'Client: Riverside Marketing - Phase 2 Maintenance', 'income', 2000.00),
(85,  '2025-12-10', 'Client: MindfulApps - Mobile App Design', 'income', 7500.00),
(86,  '2025-12-18', 'Client: BlueSky Media - Monthly Retainer', 'income', 1500.00),
(87,  '2025-12-22', 'Client: GreenLeaf Co - Holiday Campaign', 'income', 3000.00),
(88,  '2025-12-28', 'Interest - Marcus Savings', 'income', 52.30);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(83,  2, 31, 1500.00, 'credit', 'Direct deposit - TechStart retainer'),
(84,  4, 30, 2000.00, 'credit', 'Stripe deposit - Riverside maintenance'),
(85,  4, 30, 7500.00, 'credit', 'Stripe deposit - MindfulApps'),
(86,  2, 31, 1500.00, 'credit', 'Direct deposit - BlueSky retainer'),
(87,  3, 30, 3000.00, 'credit', 'PayPal deposit - GreenLeaf Co'),
(88,  6, 33, 52.30,   'credit', 'Monthly interest');

-- Expenses - Housing & Utilities (Dec)
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(89,  '2025-12-01', 'December Rent', 'expense', 1800.00),
(90,  '2025-12-05', 'Internet - Comcast', 'expense', 79.99),
(91,  '2025-12-08', 'Phone - T-Mobile', 'expense', 55.00),
(92,  '2025-12-18', 'Electric Bill - ConEd', 'expense', 125.00);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(89,  1, 11, 1800.00, 'debit', 'Rent payment'),
(90,  1, 27, 79.99,   'debit', 'Internet service'),
(91,  1, 28, 55.00,   'debit', 'Mobile plan'),
(92,  1, 29, 125.00,  'debit', 'Electric bill - winter');

-- Expenses - Business (Dec)
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(93,  '2025-12-01', 'Figma - Pro Plan', 'expense', 15.00),
(94,  '2025-12-01', 'GitHub - Team Plan', 'expense', 25.00),
(95,  '2025-12-01', 'Vercel - Pro Hosting', 'expense', 20.00),
(96,  '2025-12-01', 'Google Workspace', 'expense', 12.00),
(97,  '2025-12-01', 'WeWork - Hot Desk', 'expense', 350.00),
(98,  '2025-12-08', 'Contractor: Sarah - Design Work', 'expense', 600.00),
(99,  '2025-12-15', 'Contractor: Mike - Backend Dev', 'expense', 1500.00),
(100, '2025-12-20', 'AWS Monthly', 'expense', 58.70),
(101, '2025-12-01', 'Adobe Creative Cloud', 'expense', 54.99),
(102, '2025-12-01', 'Notion - Team Plan', 'expense', 10.00),
(103, '2025-12-10', 'Business lunch with client', 'expense', 85.00);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(93,  9, 17, 15.00,   'debit', 'Figma subscription'),
(94,  9, 17, 25.00,   'debit', 'GitHub subscription'),
(95,  9, 17, 20.00,   'debit', 'Vercel hosting'),
(96,  9, 17, 12.00,   'debit', 'Google Workspace'),
(97,  2, 18, 350.00,  'debit', 'Coworking monthly'),
(98,  2, 20, 600.00,  'debit', 'Sarah - design for MindfulApps'),
(99,  2, 20, 1500.00, 'debit', 'Mike - backend for MindfulApps'),
(100, 9, 26, 58.70,   'debit', 'AWS hosting'),
(101, 9, 17, 54.99,   'debit', 'Adobe CC'),
(102, 9, 17, 10.00,   'debit', 'Notion subscription'),
(103, 9, 13, 85.00,   'debit', 'Client lunch');

-- Expenses - Food & Dining (Dec)
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(104, '2025-12-01', 'Whole Foods', 'expense', 105.20),
(105, '2025-12-05', 'Trader Joes', 'expense', 72.40),
(106, '2025-12-08', 'Italian Restaurant', 'expense', 65.00),
(107, '2025-12-12', 'Whole Foods', 'expense', 98.30),
(108, '2025-12-15', 'Starbucks', 'expense', 7.25),
(109, '2025-12-18', 'Holiday dinner - Nobu', 'expense', 180.00),
(110, '2025-12-22', 'Trader Joes', 'expense', 85.60),
(111, '2025-12-24', 'Christmas groceries - Whole Foods', 'expense', 165.00),
(112, '2025-12-27', 'Blue Bottle Coffee', 'expense', 5.50),
(113, '2025-12-29', 'Sweetgreen', 'expense', 16.50);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(104, 8, 12, 105.20, 'debit', 'Weekly groceries'),
(105, 8, 12, 72.40,  'debit', 'Weekly groceries'),
(106, 8, 13, 65.00,  'debit', 'Dinner out'),
(107, 8, 12, 98.30,  'debit', 'Weekly groceries'),
(108, 7, 14, 7.25,   'debit', 'Morning coffee'),
(109, 8, 13, 180.00, 'debit', 'Holiday dinner'),
(110, 8, 12, 85.60,  'debit', 'Weekly groceries'),
(111, 8, 12, 165.00, 'debit', 'Christmas groceries'),
(112, 7, 14, 5.50,   'debit', 'Coffee'),
(113, 8, 13, 16.50,  'debit', 'Lunch');

-- Expenses - Health & Entertainment (Dec)
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(114, '2025-12-01', 'Blue Cross - Health Insurance', 'expense', 450.00),
(115, '2025-12-01', 'Equinox Gym', 'expense', 95.00),
(116, '2025-12-05', 'Netflix', 'expense', 15.49),
(117, '2025-12-05', 'Spotify', 'expense', 10.99),
(118, '2025-12-12', 'Holiday party supplies', 'expense', 75.00),
(119, '2025-12-20', 'Christmas gifts', 'expense', 350.00);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(114, 1, 23, 450.00, 'debit', 'Monthly health premium'),
(115, 1, 24, 95.00,  'debit', 'Gym membership'),
(116, 1, 25, 15.49,  'debit', 'Netflix subscription'),
(117, 1, 25, 10.99,  'debit', 'Spotify subscription'),
(118, 8, 6,  75.00,  'debit', 'Party supplies'),
(119, 8, 6,  350.00, 'debit', 'Christmas gifts');

-- Expenses - Transportation (Dec)
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(120, '2025-12-04', 'Uber to client meeting', 'expense', 32.00),
(121, '2025-12-15', 'Lyft to holiday party', 'expense', 18.50);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(120, 8, 15, 32.00,  'debit', 'Uber ride'),
(121, 8, 15, 18.50,  'debit', 'Lyft ride');

-- Transfers - December
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(122, '2025-12-05', 'Transfer to savings', 'transfer', 2000.00),
(123, '2025-12-15', 'Investment contribution', 'transfer', 1000.00),
(124, '2025-12-15', 'Roth IRA contribution', 'transfer', 500.00),
(125, '2025-12-28', 'Chase Sapphire payment', 'transfer', 800.00),
(126, '2025-12-28', 'Amex Blue payment', 'transfer', 400.00),
(127, '2025-12-06', 'Stripe to Business Checking', 'transfer', 9500.00),
(128, '2025-12-08', 'PayPal to Business Checking', 'transfer', 3000.00);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(122, 1,  NULL, 2000.00,  'debit',  'To savings'),
(122, 6,  NULL, 2000.00,  'credit', 'From checking'),
(123, 1,  NULL, 1000.00,  'debit',  'To brokerage'),
(123, 10, NULL, 1000.00,  'credit', 'Monthly investment - extra'),
(124, 1,  NULL, 500.00,   'debit',  'To Roth IRA'),
(124, 11, NULL, 500.00,   'credit', 'Monthly Roth contribution'),
(125, 1,  NULL, 800.00,   'debit',  'CC payment'),
(125, 8,  NULL, 800.00,   'credit', 'Payment received'),
(126, 2,  NULL, 400.00,   'debit',  'CC payment'),
(126, 9,  NULL, 400.00,   'credit', 'Payment received'),
(127, 4,  NULL, 9500.00,  'debit',  'Withdraw to bank'),
(127, 2,  NULL, 9500.00,  'credit', 'From Stripe'),
(128, 3,  NULL, 3000.00,  'debit',  'Withdraw to bank'),
(128, 2,  NULL, 3000.00,  'credit', 'From PayPal');

-- Monthly transfer: business to personal for living expenses
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(202, '2025-12-02', 'Transfer from Business to Personal', 'transfer', 5500.00);
INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(202, 2, NULL, 5500.00, 'debit',  'To personal checking'),
(202, 1, NULL, 5500.00, 'credit', 'From business checking');

-- ===================== JANUARY 2026 =====================

-- Income
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(129, '2026-01-05', 'Client: TechStart Inc - Monthly Retainer', 'income', 1500.00),
(130, '2026-01-08', 'Client: MindfulApps - Phase 2', 'income', 4500.00),
(131, '2026-01-12', 'Client: Summit Analytics - Dashboard', 'income', 5000.00),
(132, '2026-01-20', 'Client: BlueSky Media - Monthly Retainer', 'income', 1500.00),
(133, '2026-01-22', 'Client: Peak Performance - Phase 2', 'income', 2500.00),
(134, '2026-01-28', 'Affiliate Commission - WPEngine', 'income', 280.00),
(135, '2026-01-30', 'Interest - Marcus Savings', 'income', 58.75);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(129, 2, 31, 1500.00, 'credit', 'Direct deposit - TechStart retainer'),
(130, 4, 30, 4500.00, 'credit', 'Stripe deposit - MindfulApps'),
(131, 4, 30, 5000.00, 'credit', 'Stripe deposit - Summit Analytics'),
(132, 2, 31, 1500.00, 'credit', 'Direct deposit - BlueSky retainer'),
(133, 5, 30, 2500.00, 'credit', 'Wise deposit - Peak Performance (UK)'),
(134, 3, 32, 280.00,  'credit', 'WPEngine affiliate payout'),
(135, 6, 33, 58.75,   'credit', 'Monthly interest');

-- Expenses - Housing & Utilities (Jan)
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(136, '2026-01-01', 'January Rent', 'expense', 1800.00),
(137, '2026-01-05', 'Internet - Comcast', 'expense', 79.99),
(138, '2026-01-08', 'Phone - T-Mobile', 'expense', 55.00),
(139, '2026-01-20', 'Electric Bill - ConEd', 'expense', 140.00);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(136, 1, 11, 1800.00, 'debit', 'Rent payment'),
(137, 1, 27, 79.99,   'debit', 'Internet service'),
(138, 1, 28, 55.00,   'debit', 'Mobile plan'),
(139, 1, 29, 140.00,  'debit', 'Electric bill - winter peak');

-- Expenses - Business (Jan)
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(140, '2026-01-01', 'Figma - Pro Plan', 'expense', 15.00),
(141, '2026-01-01', 'GitHub - Team Plan', 'expense', 25.00),
(142, '2026-01-01', 'Vercel - Pro Hosting', 'expense', 20.00),
(143, '2026-01-01', 'Google Workspace', 'expense', 12.00),
(144, '2026-01-01', 'WeWork - Hot Desk', 'expense', 350.00),
(145, '2026-01-10', 'Contractor: Sarah - Design Work', 'expense', 900.00),
(146, '2026-01-18', 'AWS Monthly', 'expense', 61.20),
(147, '2026-01-01', 'Adobe Creative Cloud', 'expense', 54.99),
(148, '2026-01-01', 'Notion - Team Plan', 'expense', 10.00),
(149, '2026-01-15', 'New monitor - Dell Ultrawide', 'expense', 650.00),
(150, '2026-01-08', 'Online course - Advanced React Patterns', 'expense', 49.00);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(140, 9,  17, 15.00,   'debit', 'Figma subscription'),
(141, 9,  17, 25.00,   'debit', 'GitHub subscription'),
(142, 9,  17, 20.00,   'debit', 'Vercel hosting'),
(143, 9,  17, 12.00,   'debit', 'Google Workspace'),
(144, 2,  18, 350.00,  'debit', 'Coworking monthly'),
(145, 2,  20, 900.00,  'debit', 'Sarah - design for Summit'),
(146, 9,  26, 61.20,   'debit', 'AWS hosting'),
(147, 9,  17, 54.99,   'debit', 'Adobe CC'),
(148, 9,  17, 10.00,   'debit', 'Notion subscription'),
(149, 9,  19, 650.00,  'debit', 'Dell monitor'),
(150, 9,  10, 49.00,   'debit', 'React course');

-- Expenses - Food & Dining (Jan)
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(151, '2026-01-03', 'Whole Foods', 'expense', 88.90),
(152, '2026-01-07', 'Trader Joes', 'expense', 64.20),
(153, '2026-01-10', 'Ramen Bar', 'expense', 22.00),
(154, '2026-01-14', 'Whole Foods', 'expense', 91.50),
(155, '2026-01-17', 'Starbucks', 'expense', 6.75),
(156, '2026-01-19', 'Dim Sum Palace', 'expense', 55.00),
(157, '2026-01-23', 'Trader Joes', 'expense', 59.80),
(158, '2026-01-26', 'Sweetgreen', 'expense', 16.50),
(159, '2026-01-28', 'Whole Foods', 'expense', 82.40),
(160, '2026-01-30', 'Blue Bottle Coffee', 'expense', 5.50);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(151, 8,  12, 88.90,  'debit', 'Weekly groceries'),
(152, 8,  12, 64.20,  'debit', 'Weekly groceries'),
(153, 8,  13, 22.00,  'debit', 'Lunch out'),
(154, 8,  12, 91.50,  'debit', 'Weekly groceries'),
(155, 7,  14, 6.75,   'debit', 'Morning coffee'),
(156, 8,  13, 55.00,  'debit', 'Dinner out'),
(157, 8,  12, 59.80,  'debit', 'Weekly groceries'),
(158, 8,  13, 16.50,  'debit', 'Lunch'),
(159, 8,  12, 82.40,  'debit', 'Weekly groceries'),
(160, 7,  14, 5.50,   'debit', 'Coffee');

-- Expenses - Health & Entertainment (Jan)
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(161, '2026-01-01', 'Blue Cross - Health Insurance', 'expense', 450.00),
(162, '2026-01-01', 'Equinox Gym', 'expense', 95.00),
(163, '2026-01-05', 'Netflix', 'expense', 15.49),
(164, '2026-01-05', 'Spotify', 'expense', 10.99),
(165, '2026-01-18', 'Escape Room', 'expense', 40.00),
(166, '2026-01-25', 'Comedy Show', 'expense', 60.00);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(161, 1, 23, 450.00,  'debit', 'Monthly health premium'),
(162, 1, 24, 95.00,   'debit', 'Gym membership'),
(163, 1, 25, 15.49,   'debit', 'Netflix subscription'),
(164, 1, 25, 10.99,   'debit', 'Spotify subscription'),
(165, 8, 6,  40.00,   'debit', 'Escape room'),
(166, 8, 6,  60.00,   'debit', 'Comedy show');

-- Expenses - Transportation (Jan)
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(167, '2026-01-06', 'Uber to coworking', 'expense', 18.00),
(168, '2026-01-14', 'Uber to client meeting', 'expense', 25.50),
(169, '2026-01-22', 'Lyft to airport', 'expense', 48.00);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(167, 8, 15, 18.00,  'debit', 'Uber ride'),
(168, 8, 15, 25.50,  'debit', 'Uber to client'),
(169, 8, 15, 48.00,  'debit', 'Lyft to JFK');

-- Transfers - January
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(170, '2026-01-05', 'Transfer to savings', 'transfer', 1500.00),
(171, '2026-01-15', 'Investment contribution', 'transfer', 1000.00),
(172, '2026-01-15', 'Roth IRA contribution', 'transfer', 500.00),
(173, '2026-01-28', 'Chase Sapphire payment', 'transfer', 700.00),
(174, '2026-01-28', 'Amex Blue payment', 'transfer', 500.00),
(175, '2026-01-09', 'Stripe to Business Checking', 'transfer', 9500.00),
(176, '2026-01-23', 'Wise to Business Checking', 'transfer', 2500.00),
(177, '2026-01-12', 'PayPal to Personal Checking', 'transfer', 280.00);

INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(170, 1,  NULL, 1500.00,  'debit',  'To savings'),
(170, 6,  NULL, 1500.00,  'credit', 'From checking'),
(171, 1,  NULL, 1000.00,  'debit',  'To brokerage'),
(171, 10, NULL, 1000.00,  'credit', 'Monthly investment'),
(172, 1,  NULL, 500.00,   'debit',  'To Roth IRA'),
(172, 11, NULL, 500.00,   'credit', 'Monthly Roth contribution'),
(173, 1,  NULL, 700.00,   'debit',  'CC payment'),
(173, 8,  NULL, 700.00,   'credit', 'Payment received'),
(174, 2,  NULL, 500.00,   'debit',  'CC payment'),
(174, 9,  NULL, 500.00,   'credit', 'Payment received'),
(175, 4,  NULL, 9500.00,  'debit',  'Withdraw to bank'),
(175, 2,  NULL, 9500.00,  'credit', 'From Stripe'),
(176, 5,  NULL, 2500.00,  'debit',  'Withdraw to bank'),
(176, 2,  NULL, 2500.00,  'credit', 'From Wise'),
(177, 3,  NULL, 280.00,   'debit',  'Withdraw to personal'),
(177, 1,  NULL, 280.00,   'credit', 'From PayPal');

-- Monthly transfer: business to personal for living expenses
INSERT INTO transactions (id, transaction_date, description, transaction_type, total_amount) VALUES
(203, '2026-01-02', 'Transfer from Business to Personal', 'transfer', 5500.00);
INSERT INTO transaction_entries (transaction_id, account_id, category_id, amount, entry_type, description) VALUES
(203, 2, NULL, 5500.00, 'debit',  'To personal checking'),
(203, 1, NULL, 5500.00, 'credit', 'From business checking');

-- Set the sequence to max ID
SELECT setval('transactions_id_seq', 203);

-- =============================================================================
-- 5. CALCULATE ACCOUNT BALANCES
-- =============================================================================

-- Reset balances to opening_balance, then apply all entries
UPDATE accounts a SET current_balance = a.opening_balance + COALESCE((
    SELECT SUM(CASE WHEN te.entry_type = 'credit' THEN te.amount ELSE -te.amount END)
    FROM transaction_entries te
    WHERE te.account_id = a.id
), 0);

-- =============================================================================
-- 6. BUDGETS (Dec 2025 – Feb 2026)
-- =============================================================================

INSERT INTO budgets (category_id, month_year, budgeted_amount) VALUES
-- December 2025
(11, '2025-12-01', 1800.00),  -- Rent
(12, '2025-12-01', 500.00),   -- Groceries
(13, '2025-12-01', 300.00),   -- Dining Out
(17, '2025-12-01', 250.00),   -- Software Subscriptions
(18, '2025-12-01', 400.00),   -- Coworking
(6,  '2025-12-01', 300.00),   -- Entertainment
(25, '2025-12-01', 50.00),    -- Streaming
-- January 2026
(11, '2026-01-01', 1800.00),  -- Rent
(12, '2026-01-01', 500.00),   -- Groceries
(13, '2026-01-01', 300.00),   -- Dining Out
(17, '2026-01-01', 250.00),   -- Software Subscriptions
(18, '2026-01-01', 400.00),   -- Coworking
(6,  '2026-01-01', 200.00),   -- Entertainment
(25, '2026-01-01', 50.00),    -- Streaming
(19, '2026-01-01', 500.00),   -- Equipment
-- February 2026
(11, '2026-02-01', 1800.00),  -- Rent
(12, '2026-02-01', 500.00),   -- Groceries
(13, '2026-02-01', 300.00),   -- Dining Out
(17, '2026-02-01', 250.00),   -- Software Subscriptions
(18, '2026-02-01', 400.00),   -- Coworking
(6,  '2026-02-01', 200.00),   -- Entertainment
(25, '2026-02-01', 50.00),    -- Streaming
(19, '2026-02-01', 500.00);   -- Equipment

-- Update actual_amount from transaction entries
UPDATE budgets b SET actual_amount = COALESCE((
    SELECT SUM(te.amount)
    FROM transaction_entries te
    JOIN transactions t ON te.transaction_id = t.id
    WHERE te.category_id = b.category_id
      AND t.transaction_type = 'expense'
      AND date_trunc('month', t.transaction_date::timestamp) = b.month_year
), 0);

-- =============================================================================
-- Done! Demo database ready.
-- Run: uv run python app.py
-- =============================================================================

-- =============================================================================
-- 7. CUSTOMER SCOPING FOR SINGLE-USER DEMO
-- =============================================================================

CREATE TABLE IF NOT EXISTS customers (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    email VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO customers (id, name, email)
VALUES (1, 'Demo Customer', 'demo@cashy.local')
ON CONFLICT (id) DO NOTHING;

ALTER TABLE accounts ADD COLUMN IF NOT EXISTS customer_id INTEGER;
UPDATE accounts SET customer_id = 1 WHERE customer_id IS NULL;
ALTER TABLE accounts ALTER COLUMN customer_id SET DEFAULT 1;
ALTER TABLE accounts ALTER COLUMN customer_id SET NOT NULL;
ALTER TABLE accounts ADD CONSTRAINT IF NOT EXISTS accounts_customer_fk
    FOREIGN KEY (customer_id) REFERENCES customers (id);
CREATE INDEX IF NOT EXISTS idx_accounts_customer_id ON accounts(customer_id);
