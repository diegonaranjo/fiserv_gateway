import os
import json
import logging
from datetime import datetime
from odoo import models, api

_logger = logging.getLogger(__name__)

class FiservTransactionLog(models.Model):
    """
    FiservTransactionLog manages detailed logging operations for the Fiserv payment module.

    Organizes logs into subdirectories by type:
    - transactions/: Normal transaction logs 
    - errors/: Error logs
    - notifications/: Gateway notification logs
    - debug/: Debug logs
    - misc/: Uncategorized logs

    File naming convention:
    {prefix}_{reference}_{timestamp}.log

    Main methods:
    - save_transaction_log(): Base method for log saving
    - log_error(): Records errors
    - log_notification(): Records notifications
    - log_debug(): Records debug information

    Args:
    log_data (dict): Data to be logged
    filename_prefix (str): Optional prefix for filename
    log_type (str): Type of log ('transaction', 'error', 'notification', 'debug')

    Returns:
    bool: True if log saved successfully, False on error

    Usage:
    # Log error
    self.env['fiserv.transaction.log'].log_error({
        'transaction_id': tx_id,
        'error_message': error
    })

    # Log transaction
    self.env['fiserv.transaction.log'].save_transaction_log({
        'transaction_id': tx_id, 
        'amount': amount
    })
    """
    _name = 'fiserv.transaction.log'
    _description = 'Fiserv Transaction Logs'

    @api.model
    def save_transaction_log(self, log_data, filename_prefix=None, log_type='transaction'):
        try:
            base_dir = '/var/log/odoo/fiserv'
            
            # Subdirectories by record type
            log_types = {
                'transaction': 'transactions', 
                'error': 'errors',
                'notification': 'notifications',
                'debug': 'debug'
            }
            
            # Create directory structure
            for directory in log_types.values():
                os.makedirs(os.path.join(base_dir, directory), exist_ok=True)
            
            subdir = log_types.get(log_type, 'misc')
            log_dir = os.path.join(base_dir, subdir)
            
            # Generate timestamp 
            timestamp = log_data.get('timestamp')
            if not isinstance(timestamp, str):
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            else:
                timestamp = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S').strftime('%Y%m%d_%H%M%S')

            # Get reference
            reference = log_data.get('transaction_reference')
            if not reference:
                reference = f"TX{log_data.get('transaction_id', 'unknown')}"

            # Build filename without timestamp
            prefix = filename_prefix or f'fiserv_{subdir}'
            filename = f'{prefix}_{reference}.log'
            filepath = os.path.join(log_dir, filename)

            # Add timestamp to log_data
            log_data.update({
                'log_type': log_type,
                'timestamp': timestamp,
                'log_filename': filename
            })

            # Read existing logs or start new list
            existing_logs = []
            if os.path.exists(filepath):
                with open(filepath, 'r') as f:
                    try:
                        existing_logs = json.load(f)
                        if not isinstance(existing_logs, list):
                            existing_logs = [existing_logs]
                    except json.JSONDecodeError:
                        existing_logs = []

            # Add new log
            serializable_data = json.loads(json.dumps(log_data, default=str))
            existing_logs.append(serializable_data)

            # Save updated logs
            with open(filepath, 'w') as f:
                json.dump(existing_logs, f, indent=4, ensure_ascii=False)
            return True
            
        except Exception as e:
            return False

    def log_error(self, error_data, filename_prefix=None):
        return self.save_transaction_log(error_data, filename_prefix, 'error')

    def log_notification(self, notification_data, filename_prefix=None):
        return self.save_transaction_log(notification_data, filename_prefix, 'notification')

    def log_debug(self, debug_data, filename_prefix=None):
        return self.save_transaction_log(debug_data, filename_prefix, 'debug')
