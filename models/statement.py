from odoo import fields,models,api,_
from odoo.tests.common import Form
from datetime import datetime
from odoo.exceptions import UserError, ValidationError

#
# class AccountPaymentRegister(models.TransientModel):
#     _inherit = 'account.payment.register'
#
#     def action_create_payments(self):
#         res = super(AccountPaymentRegister, self).action_create_payments()
#         j = self.env['account.payment.method'].search([('name', '=', 'Manual')])[0]
#         # stmt = self.env['account.bank.statement']
#         # if not stmt:
#         #     journ = self.journal_id
#         #     if self.env['account.bank.statement'].search(
#         #             [('company_id', '=', journ.company_id.id), ('journal_id', '=', journ.id)]):
#         #         bal = self.env['account.bank.statement'].search(
#         #             [('company_id', '=', journ.company_id.id), ('journal_id', '=', journ.id)])[
#         #             0].balance_end_real
#         #     else:
#         #         bal = 0
#         #     if self.partner_type == 'customer':
#         #             stmt = self.env['account.bank.statement'].create({'name': self.journal_id.company_id.partner_id.name,
#         #                                                               'balance_start': bal,
#         #                                                               'journal_id': self.journal_id.id,
#         #                                                               'balance_end_real': bal + self.amount
#         #
#         #                                                               })
#         #             payment_list = []
#         #             product_line = (0, 0, {
#         #                 'date': self.payment_date,
#         #                 'name': self.communication,
#         #                 'partner_id': self.partner_id.id,
#         #                 'payment_ref': self.communication,
#         #                 'amount': self.amount
#         #             })
#         #
#         #             payment_list.append(product_line)
#         #     else:
#         #         stmt = self.env['account.bank.statement'].create({'name': self.journal_id.company_id.partner_id.name,
#         #                                                           'balance_start': bal,
#         #                                                           'journal_id': self.journal_id.id,
#         #                                                           'balance_end_real': bal - self.amount
#         #
#         #                                                           })
#         #         payment_list = []
#         #         product_line = (0, 0, {
#         #             'date': self.payment_date,
#         #             'name': self.communication,
#         #             'partner_id': self.partner_id.id,
#         #             'payment_ref': self.communication,
#         #             'amount': - self.amount
#         #         })
#         #
#         #         payment_list.append(product_line)
#         # if stmt:
#         #     stmt.line_ids = payment_list
#         #     stmt.button_post()
#         #



class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'
    _description = 'Register Payment'

    def _create_payments(self):
        self.ensure_one()
        batches = self._get_batches()
        edit_mode = self.can_edit_wizard and (len(batches[0]['lines']) == 1 or self.group_payment)

        to_reconcile = []
        if edit_mode:
            payment_vals = self._create_payment_vals_from_wizard()
            payment_vals_list = [payment_vals]
            to_reconcile.append(batches[0]['lines'])
        else:
            # Don't group payments: Create one batch per move.
            if not self.group_payment:
                new_batches = []
                for batch_result in batches:
                    for line in batch_result['lines']:
                        new_batches.append({
                            **batch_result,
                            'lines': line,
                        })
                batches = new_batches

            payment_vals_list = []
            for batch_result in batches:
                payment_vals_list.append(self._create_payment_vals_from_batch(batch_result))
                to_reconcile.append(batch_result['lines'])

        payments = self.env['account.payment'].create(payment_vals_list)
        print('mou')
        pay_id_list = []
        for k in payments.line_ids:
            pay_id_list.append(k.id)

        if self.env['account.bank.statement'].search([]):
            if self.env['account.bank.statement'].search(
                    [('company_id', '=', payments.journal_id.company_id.id), ('journal_id', '=', payments.journal_id.id)]):
                bal = self.env['account.bank.statement'].search(
                    [('company_id', '=', payments.journal_id.company_id.id), ('journal_id', '=', payments.journal_id.id)])[
                    0].balance_end_real
            else:
                bal = 0
        else:
            credit = sum(self.env['account.move.line'].search(
                [('account_id', '=', payments.journal_id.payment_credit_account_id.id)]).mapped(
                'debit'))
            debit = sum(self.env['account.move.line'].search(
                [('account_id', '=', payments.journal_id.payment_debit_account_id.id)]).mapped(
                'debit'))
            bal = debit - credit
        final = 0
        if payments.partner_type == 'supplier':
            final  = bal - payments.amount_total
        elif  payments.partner_type == 'customer':
            final = bal + payments.amount_total
        else:
            final =bal


        stmt = self.env['account.bank.statement'].create({'name': payments.journal_id.company_id.partner_id.name,
                                                          'balance_start': bal,
                                                          'journal_id': payments.journal_id.id,
                                                          'balance_end_real': final

                                                          })
        payment_list = []
        supplier_amount = 0
        if  payments.partner_type == 'supplier':
            supplier_amount = -payments.amount_total
        else:
            supplier_amount = payments.amount_total

        product_line = (0, 0, {
            'date': payments.date,
            'name': payments.name,
            'partner_id': payments.partner_id.id,
            'payment_ref': payments.name,
            'amount': supplier_amount
        })
        payment_list.append(product_line)
        if stmt:
            stmt.line_ids = payment_list
            stmt.move_line_ids = pay_id_list
            stmt.write({'state': 'confirm'})
            stmt.move_line_ids = False
            # stmt.move_line_ids.unlink()
            # stmt.button_validate_or_action()

        # If payments are made using a currency different than the source one, ensure the balance match exactly in
        # order to fully paid the source journal items.
        # For example, suppose a new currency B having a rate 100:1 regarding the company currency A.
        # If you try to pay 12.15A using 0.12B, the computed balance will be 12.00A for the payment instead of 12.15A.
        if edit_mode:
            for payment, lines in zip(payments, to_reconcile):
                # Batches are made using the same currency so making 'lines.currency_id' is ok.
                if payment.currency_id != lines.currency_id:
                    liquidity_lines, counterpart_lines, writeoff_lines = payment._seek_for_lines()
                    source_balance = abs(sum(lines.mapped('amount_residual')))
                    payment_rate = liquidity_lines[0].amount_currency / liquidity_lines[0].balance
                    source_balance_converted = abs(source_balance) * payment_rate

                    # Translate the balance into the payment currency is order to be able to compare them.
                    # In case in both have the same value (12.15 * 0.01 ~= 0.12 in our example), it means the user
                    # attempt to fully paid the source lines and then, we need to manually fix them to get a perfect
                    # match.
                    payment_balance = abs(sum(counterpart_lines.mapped('balance')))
                    payment_amount_currency = abs(sum(counterpart_lines.mapped('amount_currency')))
                    if not payment.currency_id.is_zero(source_balance_converted - payment_amount_currency):
                        continue

                    delta_balance = source_balance - payment_balance

                    # Balance are already the same.
                    if self.company_currency_id.is_zero(delta_balance):
                        continue

                    # Fix the balance but make sure to peek the liquidity and counterpart lines first.
                    debit_lines = (liquidity_lines + counterpart_lines).filtered('debit')
                    credit_lines = (liquidity_lines + counterpart_lines).filtered('credit')

                    payment.move_id.write({'line_ids': [
                        (1, debit_lines[0].id, {'debit': debit_lines[0].debit + delta_balance}),
                        (1, credit_lines[0].id, {'credit': credit_lines[0].credit + delta_balance}),
                    ]})

        payments.action_post()

        domain = [('account_internal_type', 'in', ('receivable', 'payable')), ('reconciled', '=', False)]
        for payment, lines in zip(payments, to_reconcile):

            # When using the payment tokens, the payment could not be posted at this point (e.g. the transaction failed)
            # and then, we can't perform the reconciliation.
            if payment.state != 'posted':
                continue

            payment_lines = payment.line_ids.filtered_domain(domain)
            for account in payment_lines.account_id:
                (payment_lines + lines) \
                    .filtered_domain([('account_id', '=', account.id), ('reconciled', '=', False)]) \
                    .reconcile()

        return payments
class AccountMove(models.Model):
    _inherit = 'account.move'

    @api.constrains('name', 'journal_id', 'state')
    def _check_unique_sequence_number(self):
        moves = self.filtered(lambda move: move.state == 'posted')
        if not moves:
            return

        self.flush(['name', 'journal_id', 'move_type', 'state'])

        # /!\ Computed stored fields are not yet inside the database.
        self._cr.execute('''
            SELECT move2.id, move2.name
            FROM account_move move
            INNER JOIN account_move move2 ON
                move2.name = move.name
                AND move2.journal_id = move.journal_id
                AND move2.move_type = move.move_type
                AND move2.id != move.id
            WHERE move.id IN %s AND move2.state = 'posted'
        ''', [tuple(moves.ids)])
        res = self._cr.fetchall()
        # if res:
        #     raise ValidationError(_('Posted journal entry must have an unique sequence number per company.\n'
        #                             'Problematic numbers: %s\n') % ', '.join(r[1] for r in res))



class AccountBankStatement(models.Model):
    _inherit = "account.bank.statement"
    _order = "id desc"
