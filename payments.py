from abc import ABC, abstractmethod
from database import db_session, Wallet, WalletTransaction
from config import logger

class PaymentGatewayPlugin(ABC):
    @abstractmethod
    def generate_invoice(self, user_id: int, amount: float) -> dict:
        pass

    @abstractmethod
    def verify_payment(self, tx_id: str) -> bool:
        pass

class USDT_TRC20_Plugin(PaymentGatewayPlugin):
    def generate_invoice(self, user_id: int, amount: float) -> dict:
        crypto_address = "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"
        return {"gateway": "USDT_TRC20", "address": crypto_address, "amount": amount}

    def verify_payment(self, tx_id: str) -> bool:
        return True

class PaymentProcessor:
    def __init__(self, gateway: PaymentGatewayPlugin):
        self.gateway = gateway

    def credit_user_wallet(self, user_id: int, amount: float, tx_desc: str):
        try:
            wallet = Wallet.query.filter_by(user_id=user_id).first()
            if wallet:
                wallet.balance += amount
                tx = WalletTransaction(wallet_id=wallet.id, amount=amount, tx_type="deposit", description=tx_desc)
                db_session.add(tx)
                db_session.commit()
                logger.info(f"Credited {amount} to user {user_id}")
                return True
            return False
        except Exception as e:
            db_session.rollback()
            logger.error(f"Failed to credit wallet: {e}")
            return False
