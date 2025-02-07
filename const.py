# Moneda soportada por Fiserv Argentina en formato ISO 4217
SUPPORTED_CURRENCIES = ['ARS']

# Código de moneda para Fiserv (032 = ARS)
CURRENCY_MAPPING = {
    'ARS': '032'
}

# Códigos de tarjetas soportadas por Fiserv Argentina
SUPPORTED_CARD_BRANDS = {
    'V': {'name': 'Visa', 'credit': True, 'debit': True},
    'M': {'name': 'Mastercard', 'credit': True, 'debit': True},
    'MA': {'name': 'Maestro', 'credit': False, 'debit': True},
    'CABAL_ARGENTINA': {'name': 'Cabal', 'credit': True, 'debit': True},
    'TUYA': {'name': 'Tuya', 'credit': True, 'debit': False},
    'NARANJA': {'name': 'Naranja', 'credit': True, 'debit': False},
}

# Modos de checkout disponibles
CHECKOUT_MODES = {
    'combinedpage': 'Combined Page',
    'classic': 'Classic',
}

# Modos de pago disponibles
PAYMENT_MODES = {
    'payonly': 'Pay Only',
    'payplus': 'Pay Plus',
    'fullpay': 'Full Pay',
}

PAYMENT_STATUS_MAPPING = {
    'pending': ['P', 'PENDING', 'PROCESSING'],
    'done': ['Y', 'APPROVED', 'APROBADO'],
    'denied': ['N', 'DENIED', 'NEGADO'],
    'cancel': ['N', 'CANCELLED', 'CANCELED'],
    'declined': ['N:01', 'N:02', 'N:03', 'N:04', 'N:05', 'REJECTED']
}

# Mensajes de estado
"""
STATUS_MESSAGE_MAPPING = {
    'APPROVED': ('Pago aprobado'),
    'PENDING': ('Pago pendiente de confirmación'),
    'REJECTED': ('Pago rechazado'),
    'PROCESSING': ('Procesando pago'),
    'ERROR': ('Error en el pago')
}
"""

# Mapeo de códigos de error a mensajes
ERROR_MESSAGE_MAPPING = {
    'N:01': ("Consulte con el emisor de la tarjeta"),
    'N:02': ("Consulte con el emisor de la tarjeta sobre condiciones especiales"),
    'N:03': ("Comercio inválido"),
    'N:04': ("Tarjeta restringida"),
    'N:05': ("Transacción rechazada"),
    'N:07': ("Tarjeta reportada como robada"),
    'N:12': ("Transacción inválida"),
    'N:13': ("Monto inválido"),
    'N:14': ("Número de tarjeta inválido"),
    'N:19': ("Reintentar transacción"),
    'N:25': ("No se encontró el registro original"),
    'N:30': ("Error de formato"),
    'N:41': ("Tarjeta reportada como perdida"),
    'N:43': ("Tarjeta reportada como robada"),
    'N:51': ("Fondos insuficientes"),
    'N:54': ("Tarjeta vencida"),
    'N:55': ("PIN incorrecto"),
    'N:57': ("Transacción no permitida para esta tarjeta"),
    'N:58': ("Transacción no permitida en este terminal"),
    'N:61': ("Excede límite de monto"),
    'N:62': ("Tarjeta restringida"),
    'N:65': ("Excede límite de frecuencia"),
    'N:91': ("Emisor no disponible"),
    'N:96': ("Error del sistema"),
    'N:99': ("Error general")
}

# URLs base según ambiente
REDIR_URLS = {
    'test': 'https://test.ipg-online.com/connect/gateway/processing',
    'prod': 'https://www5.ipg-online.com/connect/gateway/processing'
}

# Configuración por defecto de 3DS
THREEDS_CONFIG = {
    'authenticateTransaction': 'true',
    'threeDSRequestorChallengeIndicator': '01'
}

# Parámetros requeridos para el pago
REQUIRED_PAYMENT_PARAMS = [
    'storename',
    'txndatetime',
    'chargetotal',
    'currency',
    'hash'
]

# Configuración de hash
HASH_CONFIG = {
    'algorithm': 'HMACSHA256',
    'timezone': 'America/Buenos_Aires'
}


# The codes of the payment methods to activate when Fiserv is activated.
DEFAULT_PAYMENT_METHOD_CODES = {
    # Primary payment method
    'tarjetas',
    # Card brands supported by Fiserv Argentina
    'visa',
    'mastercard',
    'maestro',
    'cabal',
    'naranja',
    'tuya',
    'argencard',
    'amex',
    'diners',
    'credimas',
    'favacard',
    'sidecreer',
    'patagonia365',
    'crediguia',
    'pyme_nacion',
    'qida',
    'su_credito',
    'coopeplus',
    'unired'
}