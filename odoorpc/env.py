# -*- coding: UTF-8 -*-
##############################################################################
#
#    OdooRPC
#    Copyright (C) 2014 Sébastien Alix.
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Lesser General Public License as published
#    by the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Lesser General Public License for more details.
#
#    You should have received a copy of the GNU Lesser General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
"""Supply the :class:`Environment` class to manage records more efficiently."""

import sys
import weakref

from odoorpc.service.model import Model, fields


FIELDS_RESERVED = ['id', 'ids', '__odoo__', '__osv__', '__data__', 'env']


class Environment(object):
    """An environment wraps data like the user ID, context or current database
    name, and provides an access to data model proxies.

    >>> import odoorpc
    >>> odoo = odoorpc.ODOO('localhost')
    >>> odoo.login('db_name', 'admin', 'admin')
    >>> odoo.env
    Environment(db='db_name', uid=1, context={'lang': 'fr_FR', 'tz': 'Europe/Brussels', 'uid': 1})
    """

    def __init__(self, odoo, db, uid, context):
        self._odoo = odoo
        self._db = db
        self._uid = uid
        self._context = context
        self._registry = {}
        self._dirty = weakref.WeakSet()      # set of records updated locally

    def __repr__(self):
        return "Environment(db=%s, uid=%s, context=%s)" % (
            repr(self._db), self._uid, self._context)

    @property
    def dirty(self):
        """
        .. warning::

            This property is used internally and should not be used directly.
            As such, it should not be referenced in the user documentation.

        List records having local changes.
        These changes can be committed to the server with the :func:`commit`
        method, or invalidated with :func:`invalidate`.
        """
        return self._dirty

    @property
    def context(self):
        """The context of the user connected.

        >>> odoo.login('db_name', 'admin', 'admin')
        >>> odoo.env.context
        {'lang': 'fr_FR', 'tz': 'Europe/Brussels', 'uid': 1}
        """
        return self._context

    @property
    def db(self):
        """The database currently used.

        >>> odoo.login('db_name', 'admin', 'admin')
        >>> odoo.env.db
        'db_name'
        """
        return self._db

    def commit(self):
        """Commit dirty records to the server. This method is automatically
        called when the `auto_commit` option is set to `True` (default).
        It can be useful to set the former option to `False` to get better
        performance by reducing the number of RPC requests generated.

        With `auto_commit` set to `True` (default behaviour), each time a value
        is set on a record field a RPC request is sent to the server to update
        the record:

        >>> user = odoo.env.user
        >>> user.name = "Joe"               # write({'name': "Joe"})
        >>> user.email = "joe@odoo.net"     # write({'email': "joe@odoo.net"})

        With `auto_commit` set to `False`, changes on a record are sent all at
        once when calling the :func:`commit` method:

        >>> odoo.config['auto_commit'] = False
        >>> user = odoo.env.user
        >>> user.name = "Joe"
        >>> user.email = "joe@odoo.net"
        >>> user in odoo.env.dirty
        True
        >>> odoo.env.commit()   # write({'name': "Joe", 'email': "joe@odoo.net"})
        >>> user in odoo.env.dirty
        False

        Only one RPC request is generated in the last case.
        """
        # Iterate on a new set, as we remove record during iteration from the
        # original one
        for record in set(self.dirty):
            values = {}
            for field in record._values_to_write:
                if record.id in record._values_to_write[field]:
                    value = record._values_to_write[field].pop(record.id)
                    values[field] = value
                    # Store the value in the '_values' dictionary. This
                    # operation is delegated to each field descriptor as some
                    # values can not be stored "as is" (e.g. magic tuples of
                    # 2many fields need to be converted)
                    record.__class__.__dict__[field].store(record, value)
            record.write(values)
            self.dirty.remove(record)

    def invalidate(self):
        """Invalidate the cache of records."""
        self.dirty.clear()

    @property
    def lang(self):
        """Return the current language code.

        >>> odoo.env.lang
        'fr_FR'
        """
        return self.context.get('lang', False)

    def ref(self, xml_id):
        """Return the record corresponding to the given `xml_id` (also called
        external ID).
        Raise an :class:`RPCError <odoorpc.error.RPCError>` if no record
        is found.

        >>> odoo.env.ref('base.lang_en')
        Recordset('res.lang', [1])

        :return: a :class:`odoorpc.service.model.Model` instance (recordset)
        :raise: :class:`odoorpc.error.RPCError`
        """
        model, id_ = self._odoo.execute(
            'ir.model.data', 'xmlid_to_res_model_res_id', xml_id, True)
        return self[model].browse(id_)

    @property
    def uid(self):
        """The user ID currently logged.

        >>> odoo.env.uid
        1
        """
        return self._uid

    @property
    def user(self):
        """Return the current user (as a record).

        >>> user = odoo.env.user
        >>> user
        Recordset('res.users', [1])
        >>> user.name
        'Administrator'

        :return: a :class:`odoorpc.service.model.Model` instance
        :raise: :class:`odoorpc.error.RPCError`
        """
        return self['res.users'].browse(self.uid)

    @property
    def registry(self):
        """The data model registry. It is a mapping between a model name and
        its corresponding proxy used to generate records.
        As soon as a model is needed the proxy is added to the registry. This
        way the model proxy is ready for a further use (avoiding costly `RPC`
        queries when browsing records through relations).

        >>> odoo.env.registry
        {}
        >>> odoo.env.user.company_id.name
        "Your Company"
        >>> odoo.env.registry
        {'res.company': Model('res.company'), 'res.users': Model('res.users')}

        If you need to regenerate the model proxy, simply delete it from the
        registry:

        >>> del odoo.env.registry['res.company']

        To delete all model proxies:

        >>> odoo.env.registry.clear()
        >>> odoo.env.registry
        {}
        """
        return self._registry

    def __getitem__(self, model):
        """Return the model class corresponding to `model`.

        >>> Partner = odoo.env['res.partner']
        >>> Partner
        Model('res.partner')

        :return: a :class:`odoorpc.service.model.Model` class
        """
        if model not in self.registry:
            #self.registry[model] = Model(self._odoo, self, model)
            self.registry[model] = self._create_model_class(model)
        return self.registry[model]

    def __call__(self, context=None):
        """Return an environment based on `self` with a different
        user context.
        """
        context = self.context if context is None else context
        env = Environment(self._odoo, self._db, self._uid, context)
        env._dirty = self._dirty
        env._registry = self._registry
        return env

    def _create_model_class(self, model):
        """Generate the model proxy class.

        :return: a :class:`odoorpc.service.model.Model` class
        """
        cls_name = model.replace('.', '_')
        # Hack for Python 2 (no need to do this for Python 3)
        if sys.version_info.major < 3:
            if isinstance(cls_name, unicode):
                cls_name = cls_name.encode('utf-8')
        # Retrieve server fields info and generate corresponding local fields
        attrs = {
            '_env': self,
            '_odoo': self._odoo,
            '_name': model,
            '_columns': {},
        }
        fields_get = self._odoo.execute(model, 'fields_get')
        for field_name, field_data in fields_get.items():
            if field_name not in FIELDS_RESERVED:
                Field = fields.generate_field(field_name, field_data)
                attrs['_columns'][field_name] = Field
                attrs[field_name] = Field
        # Case where no field 'name' exists, we generate one (which will be
        # in readonly mode) in purpose to be filled with the 'name_get' method
        if 'name' not in attrs['_columns']:
            field_data = {'type': 'text', 'string': 'Name', 'readonly': True}
            Field = fields.generate_field('name', field_data)
            attrs['_columns']['name'] = Field
            attrs['name'] = Field
        return type(cls_name, (Model,), attrs)

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
