from src.tools.finance_query import finance_db_query
from src.tools.account_balance import get_account_balance
from src.tools.recent_transactions import get_recent_transactions
from src.tools.spending_by_category import get_spending_by_category
from src.tools.all_accounts import get_all_accounts
from src.tools.create_transaction import create_transaction
from src.tools.update_transaction import update_transaction
from src.tools.delete_transaction import delete_transaction
from src.tools.generate_chart import generate_chart
from src.tools.bank_policy_search import search_bank_policies
from src.tools.block_account import block_account
from src.tools.request_manual_service_review import request_manual_service_review
from src.tools.update_customer_profile import update_customer_profile

all_tools = [
    finance_db_query,
    get_account_balance,
    get_recent_transactions,
    get_spending_by_category,
    get_all_accounts,
    create_transaction,
    update_transaction,
    delete_transaction,
    generate_chart,
    search_bank_policies,
    block_account,
    request_manual_service_review,
    update_customer_profile,
]
