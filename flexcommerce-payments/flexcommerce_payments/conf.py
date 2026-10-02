from flexcommerce_core.conf import fc_setting, register_defaults

DEFAULTS = {
    # name -> dotted path. Add your own gateways here (Stripe, Opay, Monnify...).
    "PAYMENT_GATEWAYS": {
        "paystack": "flexcommerce_payments.gateways.paystack.PaystackGateway",
        "flutterwave": "flexcommerce_payments.gateways.flutterwave.FlutterwaveGateway",
        "bank_transfer": "flexcommerce_payments.gateways.offline.BankTransferGateway",
        "pay_on_delivery": "flexcommerce_payments.gateways.offline.PayOnDeliveryGateway",
        "wallet": "flexcommerce_payments.gateways.wallet.WalletGateway",
    },
    # Paystack
    "PAYSTACK_SECRET_KEY": "",
    "PAYSTACK_PUBLIC_KEY": "",
    "PAYSTACK_BASE_URL": "https://api.paystack.co",
    # Flutterwave (v3)
    "FLUTTERWAVE_SECRET_KEY": "",
    "FLUTTERWAVE_PUBLIC_KEY": "",
    "FLUTTERWAVE_WEBHOOK_HASH": "",  # the "secret hash" set in the Flutterwave dashboard
    "FLUTTERWAVE_BASE_URL": "https://api.flutterwave.com/v3",
    # Offline methods
    "BANK_TRANSFER_ACCOUNTS": [],  # [{"bank_name": ..., "account_name": ..., "account_number": ...}]
    "PAY_ON_DELIVERY_ENABLED": True,
    # Where gateways send the customer back after paying. Client-supplied callback
    # URLs must use one of PAYMENT_ALLOWED_CALLBACK_HOSTS (open-redirect protection).
    "PAYMENT_CALLBACK_URL": "",
    "PAYMENT_ALLOWED_CALLBACK_HOSTS": [],
    "PAYMENT_HTTP_TIMEOUT": 30,
    # Wallet
    "WALLET_ENABLED": True,
    "WALLET_MAX_BALANCE": None,
    "WALLET_MIN_TOPUP": 100,
}

register_defaults(DEFAULTS)


def payments_setting(key):
    return fc_setting(key, DEFAULTS.get(key))
