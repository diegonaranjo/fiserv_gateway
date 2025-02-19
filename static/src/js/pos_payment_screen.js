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
        this.originalCardsMethodTotal = 0;
    }

    // Getters for commonly accessed properties
    get state() {
        return this.screen.state;
    }

    get selectedPaymentLine() {
        const line = this.currentOrder?.selected_paymentline || null;
        if (!line) {
            return null;
        }
        return line;
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

        this.screen.state.originalOrderTotal = this.currentOrder?.get_total_with_tax() || 0;

        // We save the original amount of the payment method here
        this.originalCardsMethodTotal = this.baseAmount;

        // Initial order log
        console.log('💰 Initial Order Total:', {
            originalCardsMethodTotal: this.originalCardsMethodTotal,
            originalOrderTotal: this.screen.state.originalOrderTotal,
            currentTotal: this.currentOrder?.get_total_with_tax()
        });
        // Show payment interface
        this.screen.state.isVisible = true;
    }

    /**
     *  Retrieves the amount from the payment method.
     */
    get baseAmount() {
        const cardPaymentLine = this.currentOrder?.payment_ids.find(
            line => line.payment_method_id?.type === 'bank'
        );
        return cardPaymentLine?.get_amount() || 0;
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

            // We only update the status
            this.screen.state.cardBrands = data.map(brand => ({
                id: brand[0],  // code
                name: brand[1] // name
            }));

            // We update visibility
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

        // First reset all state and prices
        this._resetState();
        this._resetPrices();

        // If no card brand is selected, end here
        if (!cardBrand) {
            return;
        }

        try {
            const order = this.currentOrder;
            if (!order) throw new Error('No active order');

            // Get current amount
            let currentAmount = this.baseAmount;

            // If base amount differs from original, use original
            if (this.state.originalCardsMethodTotal &&
                this.state.originalCardsMethodTotal !== currentAmount) {
                currentAmount = this.state.originalCardsMethodTotal;
            }

            // Reset payment line to correct amount
            const paymentLine = this.selectedPaymentLine;
            if (paymentLine) {
                paymentLine.set_amount(currentAmount);
            }

            if (!currentAmount || currentAmount <= 0) {
                throw new Error('Invalid payment amount');
            }

            const response = await rpc('/payment/fiserv/get_installments', {
                card_brand: cardBrand,
                amount: parseFloat(currentAmount)
            });

            if (!response?.options?.length) {
                throw new Error('No installment options available');
            }

            // Update state
            Object.assign(this.screen.state, {
                selectedCardBrand: cardBrand,
                installmentOptions: this._formatInstallmentOptions(response.options),
                totalWithInterest: currentAmount
            });

            // Update UI
            this.screen.render();

        } catch (error) {
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
            const totalWithInterest = this.originalCardsMethodTotal * option.coefficient;

            if (typeof option.coefficient === 'number' && !isNaN(option.coefficient)) {
                // 1. First update state
                Object.assign(this.state, {
                    selectedInstallments: option.installments,
                    installmentAmount: totalWithInterest / parseInt(option.installments),
                    totalWithInterest: totalWithInterest,
                    interestRate: parseFloat(option.rate)
                });

                // 2. Update payment line with new amount
                const paymentLine = this.currentOrder?.get_selected_paymentline();
                if (paymentLine) {
                    paymentLine.set_amount(totalWithInterest);
                }

                // 3. Calculate new total with all payment methods
                const totalPaid = this._recalculateOrderTotal();

                const targetTotal = totalPaid;

                // 4. Update order lines with new target total
                this._updateOrderLinesWithInterest(option.coefficient, targetTotal);

                this.screen.render();
            }
        } catch (error) {
            console.error('Error processing installment change:', error);
            this.showError('Error processing installment change');
        }
    }

    /**
    * Updates order lines with interest coefficient
    * Recalculates totals and updates UI
    * @param {number} coefficient Interest multiplier
    */
    _updateOrderLinesWithInterest(coefficient, targetTotal) {
        const order = this.currentOrder;
        if (!order) return;

        const coef = parseFloat(coefficient);
        if (isNaN(coef)) return;

        const originalOrderTotalToCalc = this.screen.state.originalOrderTotal;
        const realCoefficient = targetTotal / originalOrderTotalToCalc;

        // console.log('Original Total:', originalOrderTotalToCalc);
        // console.log('Target total final:', targetTotal);
        // console.log('Real Coefficient:', realCoefficient);

        order.get_orderlines().forEach(line => {
            try {
                if (!line.original_price) {
                    line.original_price = line.get_unit_price();
                }
                line.set_unit_price(line.original_price * realCoefficient);
            } catch (error) {
                console.error('Error processing line:', error);
            }
        });

        order.recomputeOrderData();

        if (this.screen.numberBuffer) {
            this.screen.numberBuffer.reset();
            this.screen.numberBuffer.set(targetTotal.toString());
        }
    }

    /**
     * Recalculate the order total considering all payment methods..
     */
    _recalculateOrderTotal() {
        const order = this.currentOrder;
        if (!order) return;

        let totalPaid = 0;

        // Add validation for payment_ids
        if (order.payment_ids && Array.isArray(order.payment_ids)) {
            order.payment_ids.forEach(paymentLine => {
                // console.log('Processing payment line:', paymentLine);
                if (paymentLine && typeof paymentLine.get_amount === 'function') {
                    const amount = paymentLine.get_amount();
                    // console.log('To pay:', amount);
                    totalPaid += amount;
                }
            });
        }

        // Update the total to pay on the order
        if (typeof order.set_total_paid === 'function') {
            order.set_total_paid(totalPaid);
        }

        // Recalculate order data
        if (typeof order.recomputeOrderData === 'function') {
            order.recomputeOrderData();
        }
        this.screen.render();

        //console.log('Total amount to pay:', totalPaid);

        return totalPaid;
    }

    /**
     *  Updates the payment line amount in POS orders.
     *    Key features:
     *    - Manages payment line amount updates with or without specified amounts
     *    - Calculates interest for installment payments
     *    - Updates order totals after payment modifications
     *    - Handles number buffer integration for manual amount entry
     *    - Supports automatic due amount calculation when no specific amount is provided
     */
    updatePaymentLine(amount = false) {
        this.updateReferences();

        if (!this.selectedPaymentLine) {
            return;
        }

        // If no specific amount is provided, get it from the buffer or the remaining amount
        if (amount === false) {
            amount = this.screen.numberBuffer.get()
                ? this.screen.numberBuffer.getFloat()
                : this.currentDueAmount;
        }

        // Calculate interest if there are selected installments
        if (this.state.selectedCardBrand && this.state.selectedInstallments) {
            const option = this.state.installmentOptions.find(
                opt => opt.installments.toString() === this.state.selectedInstallments.toString()
            );

            if (option) {
                amount = amount * option.coefficient;
            }
        }

        // Update the amount on the payment line
        if (amount !== null) {
            this.selectedPaymentLine.set_amount(amount);
        }

        // Recalculate the order total
        this._recalculateOrderTotal();
        // console.log('Total recalculado después de actualizar la línea de pago.');
    }

    /**
    * Updates payment line amount
    * Handles amount validation and UI updates
    * @param {number|false} amount New amount or false to get from buffer
    */
    get currentDueAmount() {
        const order = this.currentOrder;
        if (!order) return 0;

        // Get remaining amount after other payments
        const total = order.get_total_with_tax();
        const paid = order.get_total_paid();
        return total - paid;
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
     * Resets prices to original values
     * Clears interest calculations
     */
    _resetPrices() {
        const order = this.currentOrder;
        if (!order) return;

        // Restore original prices
        order.get_orderlines().forEach(line => {
            try {
                if (line.original_price) {
                    line.set_unit_price(line.original_price);
                    delete line.original_price;
                    line.setLinePrice();
                }
            } catch (error) {
                console.error('Error resetting price:', error);
            }
        });
        // Recalculate the order
        order.recomputeOrderData();
    }

    /**
     * Resets payment state to initial values
     */
    _resetState() {
        // Reset the screen status
        Object.assign(this.screen.state, {
            selectedCardBrand: null,
            installmentOptions: [],
            selectedInstallments: null,
            installmentAmount: 0,
            totalWithInterest: 0,
            interestRate: 0,
            isVisible: true,
            originalPrices: new Map()
        });

        // Reset the payment line amount to the original
        const paymentLine = this.selectedPaymentLine;
        if (paymentLine) {
            const originalAmount = this.baseAmount;
            paymentLine.set_amount(originalAmount);
        }

        // Recalculate totals
        this._recalculateOrderTotal();

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
 * - originalCardsMethodTotal: Original amount before interest
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
            originalOrderTotal: 0,
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
