import { useState } from '@odoo/owl';
import { PaymentScreen } from '@point_of_sale/app/screens/payment_screen/payment_screen';
import { usePos } from '@point_of_sale/app/store/pos_hook';
import { rpc } from '@web/core/network/rpc';
import { patch } from '@web/core/utils/patch';

/**
 * Handles Fiserv payment processing in POS payment screen
 * Manages card brand selection, installment calculation and payment updates
 */
class FiservPaymentHandler {
    constructor(screen) {
        this.screen = screen;
        this.pos = screen.pos;
        this.currentOrder = screen.currentOrder;
        this.env = screen.env;
    }

    // Getters for commonly accessed properties
    get state() {
        return this.screen.state;
    }

    get selectedPaymentLine() {
        return this.currentOrder?.selected_paymentline || null;
    }

    /**
     * Updates current order reference
     * Called when order context changes
     */
    updateReferences() {
        this.currentOrder = this.screen.currentOrder;
    }

    /**
     * Initializes Fiserv payment interface
     * Loads card brands and shows payment form
     */
    async initialize() {
        await this._loadFiservData();
        this.screen.state.isVisible = true;
    }

    /**
     * Loads available card brands from backend
     * Updates state with formatted card options
     */
    async _loadFiservData() {
        try {
            const data = await rpc('/payment/fiserv/get_card_brands');

            if (!data || !Array.isArray(data)) {
                throw new Error('Invalid card brands response format');
            }

            // Solo actualizamos el estado
            this.screen.state.cardBrands = data.map(brand => ({
                id: brand[0],  // code
                name: brand[1] // name
            }));

            // Actualizamos la visibilidad
            this.screen.state.isVisible = true;

        } catch (error) {
            console.error('Error loading card brands:', error);
            this.showError('Error al cargar marcas de tarjetas: ' + error.message);
        }
    }

    /**
     * Handles card brand selection change
     * Resets prices and loads available installments
     * @param {Event} ev Change event from select element
     */
    async onCardBrandChange(ev) {
        const cardBrand = ev.target.value;
        this._resetPrices();
        if (!cardBrand) {
            this._resetState();
            return;
        }

        try {
            const amount = this.currentOrder.get_total_with_tax();
            const response = await rpc('/payment/fiserv/get_installments', {
                card_brand: cardBrand,
                amount: parseFloat(amount)
            });

            if (response?.success && Array.isArray(response.options)) {
                this.state.selectedCardBrand = cardBrand;
                this.state.installmentOptions = this._formatInstallmentOptions(response.options);
                this.screen.render();
            } else {
                throw new Error('Invalid installment options response');
            }
        } catch (error) {
            console.error('Error loading installments:', error);
            this._resetState();
            this.showError(error.message);
        }
    }


    /**
     * Handles installment selection change
     * Updates order amounts and payment line with interest
     * @param {Event} ev Change event from select element
     */
    onInstallmentChange(ev) {
        const installments = ev.target.value;
        const option = this.state.installmentOptions.find(
            opt => opt.installments.toString() === installments
        );
        if (!option) return;

        try {
            // Update state with selected installment data
            Object.assign(this.state, {
                selectedInstallments: option.installments,
                installmentAmount: parseFloat(option.amount),
                totalWithInterest: parseFloat(option.total),
                interestRate: parseFloat(option.rate)
            });

            if (typeof option.coefficient === 'number' && !isNaN(option.coefficient)) {
                this._updateOrderLinesWithInterest(option.coefficient);

                const totalWithInterest = parseFloat(this.state.totalWithInterest);
                if (!isNaN(totalWithInterest) && this.selectedPaymentLine) {
                    // Update the payment line amount
                    this.selectedPaymentLine.set_amount(totalWithInterest);
                    this.selectedPaymentLine.set_payment_status("done");

                    // Update the number buffer if it exists
                    if (this.screen.numberBuffer) {
                        this.screen.numberBuffer.set(totalWithInterest.toString());
                    }
                }
            }

            // Trigger a re-render of the screen
            this.screen.render();
        } catch (error) {
            console.error('Error processing installment change:', error);
            this.showError('Error al procesar el cambio de cuotas');
        }
    }

    /**
        * Updates order lines with interest coefficient
        * Recalculates totals and updates UI
        * @param {number} coefficient Interest multiplier
        */
    _updateOrderLinesWithInterest(coefficient) {
        const order = this.currentOrder;
        if (!order) return;

        const coef = parseFloat(coefficient);
        if (isNaN(coef)) return;

        // Store original total before applying interest
        if (!this.state.originalTotal) {
            this.state.originalTotal = order.get_total_with_tax();
        }

        order.get_orderlines().forEach(line => {
            try {
                if (!line.original_price) {
                    line.original_price = line.get_unit_price();
                }
                line.set_unit_price(line.original_price * coef);
            } catch (error) {
                console.error('Error processing line:', error);
            }
        });

        order.recomputeOrderData();

        this.state.totalWithInterest = order.amount_total;

        // Notify payment interface of total change
        this.screen.payment_interface?.update_payment_summary({
            total: this.state.totalWithInterest,
            total_paid: order.get_total_paid(),
            remaining: order.get_due()
        });

        this._forcePaymentSummaryUpdate();
    }

    /**
        * Forces payment summary update in UI
        * Updates totals, remaining amount and payment lines
        */
    _forcePaymentSummaryUpdate() {
        if (!this.currentOrder) return;

        try {
            // Update the state with the latest totals
            Object.assign(this.screen.state, {
                totalAmount: this.state.totalWithInterest,
                remainingAmount: this.currentOrder.get_due(),
                paymentLines: [...this.currentOrder.payment_ids]
            });

            // Update the number buffer if it exists
            if (this.screen.numberBuffer) {
                this.screen.numberBuffer.set(this.state.totalWithInterest.toString());
            }

            // Notify the payment interface of the total change
            this.screen.payment_interface?.update_payment_summary({
                total: this.state.totalWithInterest,
                total_paid: this.currentOrder.get_total_paid(),
                remaining: this.currentOrder.get_due()
            });

            // Force a re-render of the screen
            this.screen.render();
        } catch (error) {
            console.error('Error updating payment summary:', error);
            this.showError('Error actualizando el resumen de pago');
        }
    }

    /**
        * Updates payment summary with new totals
        * Handles payment line amount updates
        */
    _updatePaymentSummary() {
        if (!this.currentOrder || !this.selectedPaymentLine) return;

        const paid = this.currentOrder.get_total_paid();
        const remaining = Math.max(this.state.totalWithInterest - paid, 0);

        // Update order first
        this.currentOrder.set_total_with_tax(this.state.totalWithInterest);

        this.currentOrder.recomputeOrderData();

        // Update payment line amount
        this.selectedPaymentLine.set_amount(this.state.totalWithInterest);

        // Trigger order change to refresh UI
        this.currentOrder.trigger('change', this.currentOrder);


        // Notify payment interface
        this.screen.payment_interface?.update_payment_summary({
            total: this.state.totalWithInterest,
            total_paid: paid,
            remaining: remaining
        });

        Object.assign(this.screen.state, {
            totalAmount: this.state.totalWithInterest,
            remainingAmount: remaining,
            paymentLines: [...this.currentOrder.payment_ids]
        });

        this.screen.render();
    }

    /**
        * Updates payment line amount
        * Handles amount validation and UI updates
        * @param {number|false} amount New amount or false to get from buffer
        */
    updatePaymentLine(amount = false) {
        this.updateReferences();
        if (amount === false) {
            amount = this.screen.numberBuffer.getFloat();
        }

        if (amount === null) {
            this._resetPrices();
            this.screen.deletePaymentLine(this.screen.selectedPaymentLine.uuid);
            return;
        }

        const maxAmount = this.state.totalWithInterest || this.currentOrder.get_total_with_tax();
        if (amount > maxAmount) {
            amount = maxAmount;
            this.screen.numberBuffer.set(maxAmount.toString());
            this._showMaxAmountError();
        }

        // Update the payment line amount
        this.screen.selectedPaymentLine.set_amount(amount);

        // Force a re-render of the screen
        this.screen.render();
    }


    /**
     * Resets prices to original values
     * Clears interest calculations
     */
    _resetPrices() {
        const order = this.currentOrder;
        if (!order) return;

        order.get_orderlines().forEach(line => {
            try {
                if (line.original_price) {
                    line.set_unit_price(line.original_price);
                    line.setLinePrice();
                }
            } catch (error) {
                console.error('Error resetting price:', error);
            }
        });

        order.recomputeOrderData();
    }

    /**
     * Formats installment options for display
     * Calculates amounts and interest rates
     * @param {Object} options Raw installment options
     * @returns {Array} Formatted installment options
     */
    _formatInstallmentOptions(options) {
        if (!Array.isArray(options)) {
            console.error('Invalid installment options format:', options);
            return [];
        }

        try {
            const formattedOptions = options.map(option => {
                const coefficient = parseFloat(option.coefficient) || 1.0;
                const installmentCount = option.installments === 'Plan Z' ? 1 : parseInt(option.installments);
                const interestRate = ((coefficient - 1) * 100).toFixed(2);

                return {
                    installments: option.installments,
                    coefficient: coefficient,
                    amount: parseFloat(option.installment_amount),
                    total: parseFloat(option.total_with_interest),
                    rate: interestRate,
                    label: this._formatInstallmentLabel(option.installments, option.installment_amount, interestRate)
                };
            }).filter(option => option !== null);

            return formattedOptions.sort((a, b) => {
                if (a.installments === 'Plan Z') return 1;
                if (b.installments === 'Plan Z') return -1;
                return parseInt(a.installments) - parseInt(b.installments);
            });
        } catch (error) {
            console.error('Error formatting installment options:', error);
            return [];
        }
    }

    /**
     * Formats installment label for display
     * Handles special cases like Plan Z
     */
    _formatInstallmentLabel(installments, amount, interestRate) {
        const formattedAmount = this._formatCurrency(amount);

        if (installments === 'Plan Z') {
            return `Plan Z - ${formattedAmount} (${interestRate}% interés)`;
        }

        return `${installments} cuota${installments > 1 ? 's' : ''} de ${formattedAmount} (${interestRate}% interés)`;
    }

    /**
     * Resets payment state to initial values
     */
    _resetState() {
        Object.assign(this.state, {
            selectedCardBrand: null,
            installmentOptions: [],
            selectedInstallments: null,
            installmentAmount: 0,
            totalWithInterest: 0,
            originalTotal: 0,
            interestRate: 0,
            isVisible: true,
            originalPrices: new Map()
        });

        // Reset UI elements if they exist
        if (this.screen.el) {
            const installmentSelect = this.screen.el.querySelector('select[name="o_fiserv_installments"]');
            if (installmentSelect) {
                installmentSelect.innerHTML = '<option value="">Elija las cuotas</option>';
                installmentSelect.disabled = true;
            }
        }

        this.screen.render();
    }

    /**
     * Shows max amount error notification
     */
    _showMaxAmountError() {
        this.env.services.notification.add(
            'El monto no puede exceder el total de la orden con interés', {
            title: 'Advertencia',
            type: 'warning',
        }
        );
    }

    /**
     * Formats currency amount for display
     * @param {number} amount Amount to format
     * @returns {string} Formatted currency string
     */
    _formatCurrency(amount) {
        return new Intl.NumberFormat('es-AR', {
            style: 'currency',
            currency: 'ARS',
            minimumFractionDigits: 2
        }).format(amount);
    }

    /**
     * Shows error notification
     * @param {string} message Error message to display
     */
    showError(message) {
        this.env.services.notification.add(message, {
            title: 'Error',
            type: 'danger',
            sticky: false,
        });
    }
}

/**
 * Initializes PaymentScreen with Fiserv functionality.
 * Sets up state management and payment handler for card payments.
 * 
 * @override
 * State properties:
 * - selectedCardBrand: Currently selected card brand
 * - installmentOptions: Available installment plans
 * - selectedInstallments: Selected number of installments
 * - installmentAmount: Amount per installment
 * - totalWithInterest: Total amount including interest
 * - interestRate: Current interest rate
 * - cardBrands: Available card brands
 * - isVisible: Form visibility flag
 * - originalTotal: Original amount before interest
 * - originalPrices: Map of original line prices
 * 
 * @private
 */
patch(PaymentScreen.prototype, {
    setup() {
        super.setup();
        this.pos = usePos();
        this.state = useState({
            selectedCardBrand: null,
            installmentOptions: [],
            selectedInstallments: null,
            installmentAmount: 0,
            totalWithInterest: 0,
            interestRate: 0,
            cardBrands: [],
            isVisible: false,
            originalTotal: 0,
            originalPrices: new Map()
        });
        this.fiservHandler = new FiservPaymentHandler(this);
    },

    /**
     * Adds a new payment line and initializes Fiserv handler if card payment.
     * Extends parent implementation with Fiserv-specific initialization.
     * 
     * @override
     * @param {Object} paymentMethod - The payment method to add
     * @returns {Promise<void>}
     * @async
     */
    async addNewPaymentLine(paymentMethod) {
        await super.addNewPaymentLine(paymentMethod);
        if (paymentMethod.name === 'Tarjetas') {
            await this.fiservHandler.initialize();
            this.render();
        }
    },

    /**
     * Updates the selected payment line amount.
     * Handles special case for card payments through Fiserv handler.
     * 
     * @override
     * @param {number|false} amount - New amount or false to get from buffer
     * @returns {Promise<void>}
     */
    updateSelectedPaymentline(amount = false) {
        const paymentLine = this.currentOrder?.selected_paymentline;
        if (paymentLine?.payment_method.name === 'Tarjetas') {
            return this.fiservHandler.updatePaymentLine(amount);
        }
        return super.updateSelectedPaymentline(amount);
    },

    /**
     * Formats monetary amounts according to Argentine currency standards.
     * Uses Intl.NumberFormat for consistent currency formatting.
     * 
     * @private
     * @param {number} amount - The amount to format
     * @returns {string} Formatted currency string (e.g., "$ 1.234,56")
     */
    _formatCurrency(amount) {
        return new Intl.NumberFormat('es-AR', {
            style: 'currency',
            currency: 'ARS',
            minimumFractionDigits: 2
        }).format(amount);
    }
});

export default PaymentScreen;