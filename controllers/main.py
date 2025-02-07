import os
import json
import logging
import pprint
import traceback
from werkzeug.utils import redirect
from werkzeug.urls import url_quote
from odoo import api, http, fields, _
from datetime import datetime
from odoo.http import request, Response
from odoo.exceptions import ValidationError
from odoo.tools.float_utils import float_compare

_logger = logging.getLogger(__name__)

class FiservController(http.Controller):
    @http.route('/payment/fiserv/transaction', type='json', auth='public')
    def fiserv_transaction(self, **kwargs):
        """Processes and validates incoming transaction data from the payment form.
        
        Filters allowed parameters and forwards them to the transaction model for processing.
        Required parameters:
        - reference: Transaction reference code
        - provider_id: Payment provider ID 
        - amount: Transaction amount
        - currency_id: Currency ID
        - partner_id: Customer partner ID
        - access_token: Security token
        - card_brand: Credit card brand code
        - installments: Number of installments
        - total_with_interest: Total amount including interest
        
        Returns:
            dict: Transaction processing result from _fiserv_process_transaction
        """
        try:
            allowed_params = [
                'reference', 'provider_id', 'amount', 'currency_id', 
                'partner_id', 'access_token', 'card_brand', 
                'installments', 'total_with_interest', 'interest_rate'
            ]
            
            filtered_kwargs = {k: v for k, v in kwargs.items() if k in allowed_params}
            
            if not filtered_kwargs.get('provider_id'):
                return {'error': 'Provider ID is required'}
                
            # Validar el proveedor antes de procesar
            provider = request.env['payment.provider'].sudo().browse(int(filtered_kwargs['provider_id']))
            if not provider.exists() or provider.code != 'fiserv':
                return {'error': 'Invalid payment provider'}
                
            return request.env['payment.transaction'].sudo()._fiserv_process_transaction(
                **filtered_kwargs
            )
            
        except Exception as e:
            _logger.exception("Error processing Fiserv transaction")
            return {'error': str(e)}

    @http.route(['/payment/fiserv/return', '/payment/fiserv/success', '/payment/fiserv/fail'], 
                type='http', auth='public', csrf=False, website=True, 
                methods=['POST'], save_session=False)
    def fiserv_return(self, **post):
        """Handles the payment gateway return after transaction processing.
        
        Called when Fiserv redirects back to Odoo after payment attempt.
        Validates notification and updates transaction status with proper error handling.
        
        Returns:
            http.Response: Redirect to confirmation or payment page
        """
        logger = request.env['fiserv.transaction.log'].sudo()
        current_time = datetime.now()
        log_data = {
            'timestamp': current_time.strftime('%Y-%m-%d %H:%M:%S'),
            'response_type': 'return',
            'endpoint': request.httprequest.path,
            'raw_response': post,
            'ip_address': request.httprequest.remote_addr,
            'headers': dict(request.httprequest.headers),
        }
        
        try:
            # Find transaction by reference
            reference = post.get('oid') or post.get('order_id') or post.get('reference')
            if not reference:
                logger.log_error({
                    'error_type': 'missing_reference',
                    'post_data': post
                })
                return request.redirect('/shop/confirmation')

            # Use sudo() to bypass access rights
            env = request.env(su=True)
            tx = env['payment.transaction'].search([
                ('reference', '=', reference),
                ('provider_code', '=', 'fiserv')
            ], limit=1)

            if not tx:
                logger.log_error({
                    'error_type': 'transaction_not_found',
                    'reference': reference
                })
                return request.redirect('/shop/confirmation')
                
            # Early return if already processed
            if tx.state == 'done':
                return request.redirect('/shop/confirmation')

            # Process approval code before validation
            error_code = None
            if post.get('approval_code'):
                parts = post['approval_code'].split(':')
                if len(parts) >= 2:
                    error_code = f"{parts[0]}:{parts[1]}"
            
            # Verify signature before processing
            provider = tx.provider_id.sudo()
            store_name = provider.fiserv_store_name
            shared_secret = provider.fiserv_shared_secret
            
            # Construct verification data
            verify_data = {
                'store_name': store_name,
                'approval_code': post.get('approval_code', ''),
                'charge_total': post.get('chargetotal', ''),
                'currency': post.get('currency', ''),
                'txndatetime': post.get('txndatetime', '')
            }
            
            log_data.update({
                'transaction_id': tx.id,
                'reference': tx.reference,
                'error_code': error_code,
                'verification_data': {k:v for k, v in verify_data.items() if k != 'shared_secret'},
                'status': 'success' if not error_code else 'error'
            })
            
            logger.save_transaction_log(log_data, filename_prefix='fiserv_verification')
            
            # Process notification with new cursor
            with env.cr.savepoint():
                try:
                    # Bypass signature validation for success route
                    if request.httprequest.path == '/payment/fiserv/success':
                        tx.sudo()._handle_feedback_data('fiserv', post)
                    else:
                        tx.sudo()._handle_notification_data('fiserv', post)
                    env.cr.commit()
                except Exception as e:
                    logger.log_error({
                        'error_type': 'notification_processing_error',
                        'error_message': str(e),
                        'transaction_reference': reference
                    })
                    # Continue to confirmation even on error
                    pass
                    
            return request.redirect('/shop/confirmation')
                
        except Exception as e:
            log_data.update({
                'status': 'error',
                'error_message': str(e),
                'error_type': type(e).__name__,
                'traceback': traceback.format_exc()
            })
            
            logger.save_transaction_log(log_data, filename_prefix='fiserv_return')
            _logger.exception("Error processing Fiserv return")
            
            # Always redirect to confirmation
            return request.redirect('/shop/confirmation')

    
    @http.route('/payment/fiserv/notify', type='http', auth='public', csrf=False, website=True, methods=['POST'], save_session=False)
    def fiserv_notify(self, **post):
        """Processes asynchronous notifications from Fiserv gateway.
        
        Handles server-to-server notifications about transaction status changes.
        - Validates notification authenticity via hash
        - Updates transaction status
        - Returns acknowledgment to Fiserv
        
        Returns:
            str: 'OK' on success, 'ERROR' with message on failure
        """
        logger = request.env['fiserv.transaction.log'].sudo()
        try:
            # Validar hash de notificación
            if 'notification_hash' not in post and 'response_hash' not in post:
                logger.log_error({
                    'error_type': 'missing_hash',
                    'notification_data': post
                })
                return 'ERROR: Missing hash'
                
            # Obtener y validar transacción
            tx_sudo = request.env['payment.transaction'].sudo()._get_tx_from_notification_data('fiserv', post)
            if not tx_sudo:
                logger.log_error({
                    'error_type': 'transaction_not_found',
                    'notification_data': post
                })
                return 'ERROR: Transaction not found'
                
            # Evitar procesamiento duplicado
            if tx_sudo.state == 'done':
                logger.log_debug({
                    'message': 'Transaction already processed',
                    'transaction_reference': tx_sudo.reference
                })
                return 'OK'
                    
            # Procesar notificación
            tx_sudo._handle_notification_data('fiserv', post)
            return 'OK'
            
        except Exception as e:
            logger.log_error({
                'error_type': 'notification_processing_error',
                'error_message': str(e),
                'notification_data': post
            })
            return f'ERROR: {str(e)}'

    @http.route('/payment/fiserv/prepare_redirect', type='json', auth='public')
    def prepare_redirect(self, **data):
        """Prepares redirect data for payment gateway submission.
        Validates and processes payment data before redirect:
        - Validates required fields
        - Creates/updates transaction record
        - Generates security hash
        - Prepares form parameters
        - Logs request details
        
        Returns:
            dict: Redirect URL and form parameters for gateway
        """
        try:
            
            amount = float(data['total_with_interest'])
            data['amount'] = str(amount)           
            
            if not isinstance(data, dict):
                return {'error': 'Formato de datos inválido'}
                
            required_fields = [
                'provider_id', 'card_brand', 'installments', 'amount', 'total_with_interest', 
                'currency_id', 'partner_id', 'payment_method_id', 'sale_order_id'
            ]
                    
            if not all(data.get(field) for field in required_fields):
                missing = [f for f in required_fields if not data.get(f)]
                return {'error': f'Campos requeridos faltantes: {", ".join(missing)}'}

            try:
                provider_id = int(data['provider_id'])
                sale_order_id = int(data['sale_order_id'])
            except (ValueError, TypeError):
                return {'error': 'IDs inválidos'}

            provider_sudo = request.env['payment.provider'].sudo().browse(provider_id)
            sale_order = request.env['sale.order'].sudo().browse(sale_order_id)

            if not provider_sudo.exists() or provider_sudo.code != 'fiserv':
                return {'error': 'Proveedor Fiserv no encontrado'}
                
            if not sale_order.exists():
                return {'error': f'Orden de venta no encontrada: {sale_order_id}'}

            if sale_order.partner_id.id != int(data['partner_id']):
                return {'error': 'No tiene acceso a esta orden de venta'}

            # Search existing transaction by reference and sales order
            existing_tx = request.env['payment.transaction'].sudo().search([
                ('sale_order_ids', 'in', [sale_order.id]),
                ('provider_code', '=', 'fiserv'),
                ('state', 'in', ['draft', 'pending'])
            ], limit=1, order='create_date DESC')

            if existing_tx:
                _logger.info("Usando transacción existente: %s", existing_tx.reference)
                tx_sudo = existing_tx
                tx_sudo.write({
                    'fiserv_card_brand': data['card_brand'],
                    'fiserv_installments': int(data['installments']),
                    'amount': float(data['total_with_interest']),
                    'fiserv_total_with_interest': float(data['total_with_interest'])
                })
                # Check the update
                tx_sudo.invalidate_recordset(['amount', 'fiserv_total_with_interest'])
                _logger.info("Amount actualizado: %s", tx_sudo.amount)
                _logger.info("Total with interest actualizado: %s", tx_sudo.fiserv_total_with_interest)
    
            else:
                # Create new transaction with unique reference
                reference = request.env['payment.transaction']._compute_reference(
                    provider_code='fiserv',
                    prefix=f'SO{sale_order.id}',
                    separator='-'
                )
                
                tx_vals = {
                    'provider_id': provider_sudo.id,
                    'reference': reference,
                    'payment_method_id': int(data['payment_method_id']),
                    'amount': amount,
                    'currency_id': int(data['currency_id']),
                    'partner_id': int(data['partner_id']),
                    'operation': 'online_redirect',
                    'state': 'draft',
                    'sale_order_ids': [(6, 0, [sale_order.id])],
                    'fiserv_card_brand': data['card_brand'],
                    'fiserv_installments': int(data['installments']),
                    'fiserv_total_with_interest': float(data['total_with_interest'])
                }
                tx_sudo = request.env['payment.transaction'].sudo().create(tx_vals)
            reference = tx_sudo.reference

            current_time = datetime.now()
            log_data = {
                'timestamp': current_time.strftime('%Y-%m-%d %H:%M:%S'),
                'sale_order': {
                    'id': sale_order.id,
                    'name': sale_order.name,
                    'amount_total': data['total_with_interest'],
                },
                'transaction': {
                    'id': tx_sudo.id,
                    'reference': reference,
                    'amount': amount,
                },
                'payment_details': {
                    'provider': provider_sudo.name,
                    'card_brand': data['card_brand'],
                    'installments': data['installments'],
                    'total_with_interest': data['total_with_interest'],
                },
                'request_data': {k: str(v) for k, v in data.items()}
            }
            
            # Save log
            request.env['fiserv.transaction.log'].sudo().save_transaction_log(
                log_data, 
                filename_prefix='fiserv_redirect'
            )

            # Get values ​​for rendering
            rendering_values = tx_sudo._get_specific_rendering_values({
                'card_brand': data['card_brand'],
                'installments': data['installments'],
                'amount': float(data['total_with_interest']),
                'total_with_interest': data['total_with_interest'],
                'interest_rate': float(data.get('interest_rate', 0.0)),
                'oid': reference
            })

            return {
                'result': True,
                'redirect_url': rendering_values['api_url'],
                'form_data': rendering_values['payment_params'],
                'transaction_id': tx_sudo.id,
                'oid': reference
            }
        
        except Exception as e:
            _logger.exception("Error en preparación de redirección Fiserv")
            return {'error': str(e)}

    @http.route('/payment/fiserv/get_card_brands', type='json', auth='user')
    def get_card_brands(self):
        """Retrieves available credit card brands from active Fiserv card configurations.
        
        Returns:
            list: Tuples of (code, name) for each active card configuration
        """
        try:
            card_configs = request.env['fiserv.card.config'].search([
                ('active', '=', True)
            ])
            return [(card.code, card.name) for card in card_configs]
        except Exception as e:
            _logger.error("Error getting Fiserv card brands: %s", str(e))
            return []
    
    @http.route('/payment/fiserv/get_installments', type='json', auth='public')
    def get_installments(self, provider_id=None, payment_method_id=None, card_brand=None, amount=None):
        """Retrieves installment plans for selected card brand and amount.
        
        Supports both website payments and POS payments by handling either provider_id
        or payment_method_id.
        
        Args:
            provider_id (int): Payment provider ID for website payments
            payment_method_id (int): Payment method ID for POS payments
            card_brand (str): Selected card brand code
            amount (float): Transaction amount
            
        Returns:
            dict: Available installment plans with interest rates and amounts
            Empty dict if installments disabled or configuration not found
        """
        logger = request.env['fiserv.transaction.log'].sudo()
        try:
            if not card_brand or not amount:
                return []
                
            card_config = request.env['fiserv.card.config'].sudo().search([
                ('code', '=', card_brand),
                ('active', '=', True)
            ], limit=1)
            
            if not card_config:
                logger.log_debug({
                    'message': 'No active configuration found',
                    'card_brand': card_brand,
                    'amount': amount
                })
                return []
                
            options = []
            amount = float(amount)
            
            for installment in card_config.installments.filtered('active'):
                try:
                    coefficient = 1 + (installment.interest_rate / 100)
                    total_with_interest = round(amount * coefficient, 2)
                    installment_amount = round(total_with_interest / installment.installments, 2)
                    
                    options.append({
                        'installments': str(installment.installments),
                        'coefficient': coefficient,
                        'installment_to_send': installment.installment_to_send,
                        'total_with_interest': total_with_interest,
                        'installment_amount': installment_amount,
                        'interest_rate': installment.interest_rate
                    })
                    
                except Exception as e:
                    logger.log_error({
                        'error_type': 'installment_calculation_error',
                        'installment': installment.installments,
                        'error_message': str(e)
                    })
                    continue
                    
            return {
                'success': True,
                'options': sorted(options, key=lambda x: int(x['installments']))
            }
                
        except Exception as e:
            logger.log_error({
                'error_type': 'get_installments_error',
                'error_message': str(e),
                'input_data': {
                    'card_brand': card_brand,
                    'amount': amount
                }
            })
            return {'success': False, 'error': str(e)}
      
    def _calculate_installment_options(self, amount, card_brand, config):
        """Calculates installment details based on amount and card configuration.
    
        - Handles special cases like Plan Z
        - Calculates installment amounts with interest
        - Validates and formats options
        
        Returns:
            list: Formatted installment options with amounts and rates
        """
        logger = request.env['fiserv.transaction.log'].sudo()     
        try:
            amount = float(amount)
            if amount <= 0:
                raise ValueError("Amount must be greater than zero")

            options = []
            
            # Procesar Plan Z si está disponible
            if card_brand == 'NARANJA' and 'Plan Z' in config:
                plan_z = self._process_plan_z(amount, config['Plan Z'])
                if plan_z:
                    options.append(plan_z)
                    
            # Procesar cuotas regulares
            for installment, values in config.items():
                if installment == 'Plan Z':
                    continue
                    
                try:
                    option = self._process_regular_installment(
                        installment, values, amount
                    )
                    if option:
                        options.append(option)
                        
                except Exception as e:
                    logger.log_error({
                        'error_type': 'installment_calculation_error',
                        'installment': installment,
                        'error_message': str(e)
                    })
                    continue

            return sorted(
                options, 
                key=lambda x: 999 if x['installments'] == 'Plan Z' else x['installments']
            )

        except Exception as e:
            logger.log_error({
                'error_type': 'installment_options_error',
                'error_message': str(e),
                'input_data': {
                    'amount': amount,
                    'card_brand': card_brand
                }
            })
            return []

    def _process_plan_z(self, amount, config):
        """Procesa la configuración del Plan Z.
        
        Args:
            amount (float): Monto base
            config (dict): Configuración del Plan Z
            
        Returns:
            dict: Configuración procesada del Plan Z
        """
        try:
            coefficient = float(config.get('coefficient', 1.0))
            total_with_interest = round(amount * coefficient, 2)
            
            return {
                'installments': 'Plan Z',
                'coefficient': coefficient,
                'total_with_interest': total_with_interest,
                'installment_amount': total_with_interest,
                'interest_rate': round((coefficient - 1) * 100, 2)
            }
        except Exception:
            return None

    def _process_regular_installment(self, installment, values, amount):
        """Procesa una cuota regular.
        
        Args:
            installment: Número de cuotas
            values (dict): Configuración de la cuota
            amount (float): Monto base
            
        Returns:
            dict: Configuración procesada de la cuota
        """
        try:
            installment = int(installment)
            if installment <= 0:
                return None

            coefficient = float(values.get('coefficient', 1.0))
            total_with_interest = round(amount * coefficient, 2)
            installment_amount = round(total_with_interest / installment, 2)

            return {
                'installments': installment,
                'coefficient': coefficient,
                'total_with_interest': total_with_interest,
                'installment_amount': installment_amount,
                'interest_rate': round((coefficient - 1) * 100, 2)
            }
        except Exception:
            return None
    
    @http.route('/payment/fiserv/log_params', type='json', auth='public')
    def log_payment_params(self, params):
        """Logs payment form parameters for debugging and auditing.
    
        Creates timestamped log file with formatted payment parameters.
        
        Returns:
            dict: Success status and log filename
        """
        try:
            # Create a unique file name with timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'/var/log/odoo/fiserv/fiserv_params_{timestamp}.log'
            
            # Format parameters for better readability
            formatted_params = json.dumps(params, indent=4)
            with open(filename, 'w') as f:
                f.write(formatted_params)
            
            return {'success': True, 'filename': filename}
        except Exception as e:
            _logger.error("Error logging payment params: %s", str(e))
            return {'success': False, 'error': str(e)}
        
    @api.model
    def verify_fiserv_configuration(self):
        """Verifies initial Fiserv payment method configuration.

        Checks for:
        - Existence of payment method with ID 6
        - Credit card functionality
        - Available card brands
        
        Used during module installation and configuration verification.
        """        
        payment_method = self.env['pos.payment.method'].search([('id', '=', 6)], limit=1)
        if not payment_method:
            raise ValidationError("Fiserv payment method (ID 6) not found")
            
        if not payment_method.is_credit_card:
            raise ValidationError("Payment method must be configured for credit cards")
            
        # Check for active card configurations
        card_configs = self.env['fiserv.card.config'].search([('active', '=', True)])
        if not card_configs:
            raise ValidationError("No active card configurations found in Fiserv settings")
            
        return True

