import { rpc } from '@web/core/network/rpc';
import publicWidget from '@web/legacy/js/public/public_widget';

publicWidget.registry.PaymentForm.include({
    events: Object.assign({}, publicWidget.registry.PaymentForm.prototype.events, {
        'change select[name="o_fiserv_card_brand"]': '_onCardBrandChange',
        'change select[name="o_fiserv_installments"]': '_onInstallmentsChange'
    }),

    /**
     * @override
     */
    async start() {
        await this._super(...arguments);
        this.fiservState = {
            amount: undefined,
            providerId: undefined,
            currency: undefined,
            selectedCardBrand: undefined,
            selectedInstallments: undefined,
            interestRate: 0.0
        };
        if (this.paymentContext) {
            this.fiservState.amount = this.paymentContext.amount;
            this.fiservState.providerId = this.paymentContext.providerId;
            this.fiservState.currency = this.paymentContext.currencyId;
        }
    },

    /**
     * Prepares parameters for the Fiserv payment redirect flow.
     * Handles form submission and redirection to Fiserv's payment gateway.
     * 
     * Technical details:
     * - Builds form parameters including installments and interest data
     * - Creates and submits a hidden form for redirect
     * - Shows loading animation during redirect
     * - Implements timeout handling for failed redirects
     * - Logs transaction details for debugging
     * 
     * @private
     * @returns {Object} Base parameters extended with Fiserv-specific data
     * @throws {Error} If preparation or redirect fails
     */
    _prepareTransactionRouteParams() {
        const baseParams = this._super(...arguments);

        if (this.paymentContext.providerCode !== 'fiserv') {
            return baseParams;
        }

        try {

            const selectedInstallments = this.el.querySelector('select[name="o_fiserv_installments"]')?.value;

            // If it is payment in one installment or there are no installments selected, interest_rate will be 0
            const interestRate = (selectedInstallments === '1' || !selectedInstallments)
                ? 0
                : (this.fiservState.interestRate || 0);

            const prepareData = {
                provider_id: this.paymentContext.providerId,
                card_brand: this.el.querySelector('select[name="o_fiserv_card_brand"]')?.value,
                installments: this.el.querySelector('select[name="o_fiserv_installments"]')?.value,
                amount: this.fiservState.amount,
                total_with_interest: this.fiservState.totalWithInterest,
                interest_rate: this.fiservState.interestRate,
                currency_id: this.paymentContext.currencyId,
                partner_id: this.paymentContext.partnerId,
                access_token: this.paymentContext.accessToken,
                payment_method_id: parseInt(this.paymentContext.paymentMethodId),
                sale_order_id: this.paymentContext.transactionRoute.split('/').pop()
            };

            rpc('/payment/fiserv/prepare_redirect', prepareData)
                .then(response => {
                    if (response.error) {
                        throw new Error(response.error);
                    }

                    // Create the form HTML in a single line
                    let formHtml = '<form action="' + response.redirect_url + '" method="POST" id="payform" name="payform">';

                    // Generate all inputs on a single line
                    Object.entries(response.form_data).forEach(([key, value]) => {
                        let formattedValue = value;
                        if (key === 'chargetotal') {
                            // Remove the toFixed(2) and keep the original value
                            formattedValue = value;
                            console.log('Chargetotal sin formatear:', value);
                        }
                        formHtml += '<input type="hidden" name="' + key + '" value="' + formattedValue + '" />';
                    });

                    formHtml += '</form>';

                    // Create object for logging
                    const logData = {
                        timestamp: new Date().toISOString(),
                        url: response.redirect_url,
                        parameters: response.form_data,
                        html_form: formHtml
                    };

                    // Send data for logging
                    rpc('/payment/fiserv/log_params', { params: logData })
                        .then(logResponse => {
                            if (!logResponse.success) {
                                console.warn('[Fiserv] Error logging form data:', logResponse.error);
                            }
                        })
                        .catch(error => {
                            console.warn('[Fiserv] Error in logging:', error);
                        });

                    // Insert the form into the DOM
                    const tempDiv = document.createElement('div');
                    tempDiv.innerHTML = formHtml;
                    const form = tempDiv.firstChild;

                    // Improved redirect message
                    const messageDiv = document.createElement('div');
                    messageDiv.setAttribute('id', 'fiserv-redirect-message');
                    messageDiv.style.cssText = `
                        position: fixed;
                        top: 50%;
                        left: 50%;
                        transform: translate(-50%, -50%);
                        z-index: 10000;
                        background: white;
                        padding: 30px;
                        border-radius: 8px;
                        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
                        text-align: center;
                        min-width: 300px;
                    `;
                    messageDiv.innerHTML = `
                        <h3 style="margin: 0 0 15px; color: #2f3542;">Procesando el pedido</h3>
                        <p style="margin: 0; color: #57606f;">Estamos redirigiendo al sitio de pago seguro...</p>
                        <div style="margin-top: 20px;">
                            <div style="width: 40px; height: 40px; border: 3px solid #f1f2f6; border-top: 3px solid #3498db; border-radius: 50%; margin: 0 auto; animation: spin 1s linear infinite;"></div>
                        </div>
                    `;

                    // Add style for animation
                    const style = document.createElement('style');
                    style.textContent = `
                        @keyframes spin {
                            0% { transform: rotate(0deg); }
                            100% { transform: rotate(360deg); }
                        }
                    `;
                    document.head.appendChild(style);

                    // Add elements to DOM and submit
                    document.body.appendChild(messageDiv);
                    document.body.appendChild(form);

                    // Small delay to ensure the message is displayed
                    setTimeout(() => {
                        form.submit();
                    }, 100);

                    // Improved security timeout
                    setTimeout(() => {
                        if (!document.hidden) {
                            messageDiv.remove();
                            style.remove();
                            this._displayError(
                                "La redirección está tardando más de lo esperado. " +
                                "Por favor, verifique su conexión e intente nuevamente."
                            );
                        }
                    }, 10000);
                })
                .catch(error => {
                    console.error('[Fiserv] Error in redirect preparation:', error);
                    this._displayError(
                        error.message ||
                        "No se pudo procesar el pago. Por favor, intente nuevamente."
                    );
                });

        } catch (error) {
            this._handleError(error);
        }

        return baseParams;
    },

    /**
     * Processes the redirect payment flow for Fiserv transactions.
     * Overrides default behavior for Fiserv payments.
     * 
     * @private
     * @param {string} providerCode - The payment provider code
     * @param {number} paymentOptionId - Selected payment option ID
     * @param {string} paymentMethodCode - Payment method code
     * @param {Object} processingValues - Additional processing values
     * @returns {Promise} Resolves immediately for Fiserv, delegates to parent for others
     */
    _processRedirectFlow(providerCode, paymentOptionId, paymentMethodCode, processingValues) {
        if (providerCode !== 'fiserv') {
            return this._super(...arguments);
        }
        return Promise.resolve();
    },

    // #=== DOM MANIPULATION ===#

    /**
     * Handles expansion of the Fiserv payment form.
     * Shows/hides form elements based on payment method selection.
     * 
     * @private
     * @param {HTMLInputElement} radio - The radio button tied to payment option
     * @returns {Promise<void>}
     * @async
     */
    async _expandInlineForm(radio) {
        await this._super(...arguments);
        const providerCode = this._getProviderCode(radio);

        if (providerCode !== 'fiserv') {
            return;
        }

        const fiservForm = this.el.querySelector('.o_fiserv_payment_form');
        if (!fiservForm) {
            return;
        }

        // Update payment context data
        this.paymentContext = {
            ...this.paymentContext,
            amount: radio.dataset.amount,
            providerId: radio.value,
            providerCode: 'fiserv'
        };

        // Show form and reset selectors
        fiservForm.classList.remove('d-none');
        await this._resetInstallments();

        // Make sure the flow is redirect
        this._setPaymentFlow('redirect');
    },

    /**
     * Prepares the Fiserv inline form when payment method is selected.
     *
     * @override
     * @private
     * @param {number} providerId - Payment provider ID
     * @param {string} providerCode - Provider code ('fiserv')
     * @param {number} paymentOptionId - Selected payment option ID
     * @param {string} paymentMethodCode - Payment method code
     * @param {string} flow - Payment flow (always 'redirect' for Fiserv)
     * @return {Promise<void>}
     */
    async _prepareInlineForm(providerId, providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode !== 'fiserv') {
            return this._super(...arguments);
        }

        // Force redirect flow for Fiserv
        this._setPaymentFlow('redirect');

        try {
            // 1. Get and validate amount
            const cardSelect = this.el.querySelector('select[name="o_fiserv_card_brand"]');
            const amount = cardSelect?.dataset.amount;

            if (!amount) {
                throw new Error(_t("Amount not available"));
            }

            // 2. Update payment context
            this.paymentContext = {
                ...this.paymentContext,
                providerId,
                paymentOptionId,
                providerCode: 'fiserv',
                amount: parseFloat(amount),
                flow: 'redirect'
            };

            // 3. Prepare form
            const fiservForm = this.el.querySelector('.o_fiserv_payment_form');
            if (fiservForm) {
                // Show form
                fiservForm.classList.remove('d-none');

                // Reset card and installment selectors
                await this._resetSelectors();

                // Enable card selection
                if (cardSelect) {
                    cardSelect.disabled = false;
                }
            }

            // 4. Show required inputs
            this._showInputs();

        } catch (error) {
            this._displayError(
                _t("Configuration Error"),
                error.message || _t("Error preparing payment form")
            );
        }
    },

    // #=== INSTALLMENT HANDLING ===#

    /**
     * Resets card and installment selectors to their initial state.
     * Clears previous selections and disables installment selection.
     * 
     * @private
     * @returns {Promise<void>}
     * @async
     */
    async _resetSelectors() {
        const cardSelect = this.el.querySelector('select[name="o_fiserv_card_brand"]');
        const installmentSelect = this.el.querySelector('select[name="o_fiserv_installments"]');

        if (cardSelect && installmentSelect) {
            cardSelect.value = '';
            installmentSelect.value = '';
            installmentSelect.disabled = true;

            // Ocultar panel de información
            const infoPanel = this.el.querySelector('[name="fiserv_installment_info"]');
            if (infoPanel) {
                infoPanel.classList.add('d-none');
            }
        }
    },

    /**
     * Handles card brand selection changes.
     * Fetches available installment options for selected card.
     * Updates form state and UI elements accordingly.
     * 
     * @private
     * @param {Event} ev - Change event from card brand selector
     * @returns {Promise<void>}
     * @async
     * @throws {Error} If amount is invalid or installment fetch fails
     */
    async _onCardBrandChange(ev) {
        const cardBrand = ev.currentTarget.value;
        const cardSelect = ev.currentTarget;

        // Get amount from select dataset
        const amount = cardSelect.dataset.amount;

        // Reset installment selection and info when card brand changes
        const installmentSelect = this.el.querySelector('select[name="o_fiserv_installments"]');
        if (installmentSelect) {
            installmentSelect.innerHTML = '<option value="">Seleccione las cuotas</option>';
            installmentSelect.disabled = true;
        }

        // Hide installment info panel
        const infoPanel = this.el.querySelector('[name="fiserv_installment_info"]');
        if (infoPanel) {
            infoPanel.classList.add('d-none');
        }

        if (!cardBrand) {
            await this._resetSelectors();
            return;
        }

        try {
            // Validar datos requeridos
            if (!amount || isNaN(parseFloat(amount))) {
                throw new Error('Invalid amount');
            }

            // Actualizar estado
            this.fiservState = {
                ...this.fiservState,
                amount: parseFloat(amount),
                selectedCardBrand: cardBrand
            };

            const response = await rpc('/payment/fiserv/get_installments', {
                card_brand: cardBrand,
                amount: parseFloat(amount)
            });
            console.log('Tarjeta', cardBrand);
            console.log('Raw response from get_installments:', response);

            if (response.error) {
                throw new Error(response.error);
            }

            if (response.success && Array.isArray(response.options)) {
                this._updateInstallmentSelect(response.options);
            } else {
                throw new Error('Invalid response format');
            }
        } catch (error) {
            console.error('[Fiserv] Error loading installments:', error);
            this._displayError(error.message);
            const installmentSelect = this.el.querySelector('select[name="o_fiserv_installments"]');
            if (installmentSelect) {
                installmentSelect.disabled = true;
            }
        }
        // Actualizar el valor en el formulario
        this.paymentContext.card_brand = cardBrand;
    },

    /**
     * Updates the installment select options
     * Clears previous options and adds new ones based on card selection
     * @private
     * @param {Array} options - Array of installment options
     */
    _updateInstallmentSelect(options) {
        const select = this.el.querySelector('select[name="o_fiserv_installments"]');
        if (!select) return;

        // Clear all existing options except the default one
        select.innerHTML = '<option value="">Seleccione las cuotas</option>';

        options.forEach(option => {
            const optionEl = document.createElement('option');
            optionEl.value = option.installments;
            optionEl.textContent = this._formatInstallmentOption(option);

            // Store data for later calculations
            Object.assign(optionEl.dataset, {
                installmentAmount: parseFloat(option.installment_amount).toFixed(2),
                totalWithInterest: parseFloat(option.total_with_interest).toFixed(2),
                interestRate: parseFloat(option.interest_rate).toFixed(2)
            });

            select.appendChild(optionEl);
        });

        // Enable the select after populating options
        select.disabled = false;

        // Hide installment info panel when changing card
        const infoPanel = this.el.querySelector('[name="fiserv_installment_info"]');
        if (infoPanel) {
            infoPanel.classList.add('d-none');
        }
    },

    /**
     * Handles installment plan selection changes.
     * Updates payment amounts and interest calculations.
     * Updates hidden fields and payment context.
     * 
     * @private
     * @param {Event} ev - Change event from installment selector
     */
    _onInstallmentsChange(ev) {
        const select = ev.target;
        const option = select.options[select.selectedIndex];

        if (!option?.value) {
            this._hideInstallmentInfo();
            return;
        }

        // Update status once with all values
        this.fiservState = {
            ...this.fiservState,
            selectedInstallments: option.value,
            installmentAmount: parseFloat(option.dataset.installmentAmount),
            totalWithInterest: parseFloat(option.dataset.totalWithInterest),
            interestRate: parseFloat(option.dataset.interestRate || 0)
        };

        console.log('Interest Rate Updated:', this.fiservState.interestRate);

        // Update hidden fields
        const totalWithInterestInput = this.el.querySelector('input[name="o_fiserv_total_with_interest"]');
        const installmentsInput = this.el.querySelector('input[name="o_fiserv_installments_to_send"]');

        if (totalWithInterestInput) {
            totalWithInterestInput.value = this.fiservState.totalWithInterest;
        }
        if (installmentsInput) {
            installmentsInput.value = this.fiservState.selectedInstallments;
        }

        // Update values ​​in payment context
        this.paymentContext.installments = this.fiservState.selectedInstallments;
        this.paymentContext.total_with_interest = this.fiservState.totalWithInterest;

        this._updateInstallmentInfo(this.fiservState);
    },

    /**
     * Updates the installment information display panel.
     * Shows installment amount, interest rate and total with interest.
     * 
     * @private
     * @param {Object} data - Installment data object containing amounts and rates
     */
    _updateInstallmentInfo(data) {
        const infoPanel = document.querySelector('[name="fiserv_installment_info"]');
        infoPanel.querySelector('[name="installment-amount"]').textContent =
            this._formatCurrency(data.installmentAmount);
        infoPanel.querySelector('[name="interest-rate"]').textContent =
            parseFloat(data.interestRate).toFixed(2);
        infoPanel.querySelector('[name="total-with-interest"]').textContent =
            this._formatCurrency(data.totalWithInterest);

        infoPanel.classList.remove('d-none');
    },

    /**
     * Resets the installment selector to its initial state.
     * Clears the installment dropdown and hides the information panel.
     * 
     * @private
     */
    _resetInstallments() {
        const select = document.querySelector('select[name="o_fiserv_installments"]');
        const infoPanel = document.querySelector('[name="fiserv_installment_info"]');

        select.innerHTML = '<option value="">Select installments</option>';
        select.disabled = true;
        infoPanel.classList.add('d-none');
    },

    /**
     * Formats installment option text for display.
     * Handles special cases like Plan Z and interest rate display.
     * 
     * @private
     * @param {Object} option - Installment option data
     * @param {string} option.installments - Number of installments or 'Plan Z'
     * @param {number} option.installment_amount - Amount per installment
     * @param {number} option.interest_rate - Interest rate percentage
     * @returns {string} Formatted display text
     */
    _formatInstallmentOption(option) {
        const { installments, installment_amount, interest_rate } = option;

        if (installments === 'Plan Z') {
            return `Plan Z - ${this._formatCurrency(option.total_with_interest)}`;
        }

        const interestText = interest_rate > 0
            ? ` (${interest_rate}% interés)`
            : ' (sin interés)';

        return `${installments} cuota${installments > 1 ? 's' : ''} de ${this._formatCurrency(installment_amount)
            }${interestText}`;
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
    },

    // #=== UTILS ===#

    /**
     * Handles payment errors in a standardized way.
     * Logs errors to console and displays user-friendly message.
     * 
     * @private
     * @param {Error} error - The error object to handle
     */
    _handleError(error) {
        console.error('[Fiserv] Payment Error:', error);
        this._displayError(("Payment Error"), error.message || ("Payment processing failed"));
    },

    /**
     * Displays an error message to the user.
     * Shows message in alert box that auto-hides after 5 seconds.
     * 
     * @private
     * @param {string} message - Error message to display
     */
    _displayError(message) {
        const errorDiv = this.$('.alert-danger');
        errorDiv.removeClass('d-none').text(message);
        setTimeout(() => errorDiv.addClass('d-none'), 5000);
    }
});

export default publicWidget.registry.PaymentForm;
