## FISERV GATEWAY FOR ODOO (only for Argentina)

## Overview 🌟
Fiserv Payment Gateway integration for Odoo 18, providing secure credit card payment processing through Fiserv's platform in Argentina. This module delivers a seamless payment experience for your e-commerce operations. 🔒

## Key Features ⭐

* 💳 Secure payment processing through Fiserv's gateway
* 📊 Support for multiple card brands
* ✨ Installment payment plans with configurable interest rates
* 🔄 Real-time payment status updates
* 📝 Detailed transaction logging and tracking
* 🔌 Integration with Odoo's native payment system
* 🌐 Support for both website and backend processing
* 🤝 Compatible with Odoo 18 Community and Enterprise editions

Supported Payment Methods 💰
Credit Cards:

* 💳 Visa
* 💳 Mastercard
* 💳 American Express
* 💳 Naranja
* 💳 Cabal
* 💳 Tuya

Debit Cards:

* 💳 Visa Débito
* 💳 Mastercard Débito
* 💳 Maestro

Prerequisites 📋

* 🏢 Active Fiserv Argentina merchant account
* 🌐 SSL certificate for your domain
* 💻 Odoo 18 installation
* 🔑 Valid credentials from Fiserv: 🏪 Store ID and 🔐 Shared Secret

Important Notice ⚠️
This module requires an active Fiserv Argentina merchant account. Please ensure you have completed the registration process with Fiserv Argentina and have received your credentials before installing this module.
📞 Contact Information: Contact Fiserv Argentina to create your credentials and learn about conditions. Visit Fiserv Argentina Contact Page https://www.fiserv.com.ar/contacto/

Payment Experience 🛍️
* See how your customers will experience the payment process:
* Fiserv payment form in checkout with installment options*

![Fiserv Payment Form](https://github.com/diegonaranjo/fiserv_gateway/blob/main/wiki/images/Checkout_payment.webp)

Secure payment processing on Fiserv's platform

Configuration ⚙️

Configure your Fiserv credentials:

* 📝 Navigate to Accounting/Settings/Payment Providers
* ➕ Create or edit Fiserv configuration
* 🔑 Enter your Store ID and Shared Secret
* 💵 Configure installment plans if needed

Payment Flow Setup:

* 🔒 Enable/disable 3D Secure
* 💳 Configure supported card types
* 📊 Set up installment plans and interest rates
* 🎨 Customize payment form display


Features in Detail 📚

* 🔐 Secure Payment Processing:

PCI-compliant payment flow
3D Secure support
Tokenization capabilities

* 💰 Installment Plans:

Configurable interest rates
Multiple installment options
Clear cost breakdown for customers

* 📊 Transaction Management:

Detailed payment history
Real-time status updates
Comprehensive transaction logs

Security 🔒

* 🛡️ Secure credential storage for merchant authentication
* 🔒 Redirect-based payment flow through Fiserv's secure payment page
* 🌐 No card data processing on Odoo server
* 📡 Secure communication between Odoo and Fiserv gateway

Technical Requirements 💻

* 🐍 Python 3.8+
* 🔧 Odoo 18
* 🌐 SSL Certificate

* 📦 Required Python packages:

* requests
* cryptography

Test Mode 🧪

For testing transactions, use the test cards provided in Credit_cards_test.txt along with your Fiserv test credentials. You'll find:

* 🔑 Test merchant credentials provide by Fiserv
* 💳 Test card numbers
* 📅 Valid expiration dates
* 🔒 Security codes (CVV)

⚠️ These cards only works for test environments.


Pending Improvements 🔧

- [ ] Enhanced admin interface for installment and interest rate management

- [ ] Optional: Support for PCI Compliance direct payment without redirection to Fiserv site

Support & Issues 🆘

* 🐛 Report issues via GitHub Issues
* 📚 Documentation available in our wiki

Contributing 🤝
We welcome contributions! Please follow these steps:

* 🍴 Fork the repository
* 🔄 Create a feature branch
* ✍️ Make your changes
* 📤 Submit a pull request

License 📄

This project is licensed under LGPL-3 - see the LICENSE file for details.

Author Note 📝

This project began in October 2024 as a solution to enable credit card payments in Argentina through Fiserv's payment gateway. The initial development focused on creating a reliable and secure payment integration for the Odoo community.

Disclaimer ⚠️
  
This is an unofficial, community-driven project. Fiserv® is a registered trademark and is not responsible for this integration. This module is maintained by the open-source community, and all support is provided through community contributions. The developers make no warranties about the functionality or reliability of this integration.
This project is not affiliated with, endorsed by, or sponsored by Fiserv. Use at your own discretion and ensure compliance with Fiserv's terms of service and local regulations.
